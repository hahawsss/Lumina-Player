"""libmpv 的 ctypes 绑定 + 事件泵 + 与 MpvController 同形的控制面。

换成 in-process libmpv 的唯一理由：画面要画进我们自己的控件里（真单窗口）。
实测过 GL 渲染路径下 VideoToolbox 硬解仍然生效（hwdec-current=videotoolbox-copy），
所以这不是拿功能换外观。

对外接口刻意和 engine.MpvController 对齐：open_media / tracks_of / select_track /
add_subtitle / set_hwdec / poll_position / quit …，UI 与管线层不需要知道底下是谁。
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import threading
import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from . import config as cfg

# 全部对着 /opt/homebrew/include/mpv/{client.h,render.h} 抄的，别凭记忆改：
# 事件号猜错不会报错，只会静悄悄收不到（EV_PROPERTY_CHANGE 是 22，不是 15）。
F_NONE, F_STRING, F_OSD, F_FLAG, F_INT64, F_DOUBLE = 0, 1, 2, 3, 4, 5
F_NODE, F_NODE_ARRAY, F_NODE_MAP, F_BYTE_ARRAY = 6, 7, 8, 9

EV_NONE, EV_SHUTDOWN, EV_LOG_MESSAGE = 0, 1, 2
EV_START_FILE, EV_END_FILE, EV_FILE_LOADED = 6, 7, 8
EV_PROPERTY_CHANGE = 22

P_API_TYPE, P_GL_INIT, P_GL_FBO, P_FLIP_Y = 1, 2, 3, 4
P_BLOCK_TARGET = 12

_OWN = threading.Lock()
_LIB: ctypes.CDLL | None = None


class NodeUnion(ctypes.Union):
    _fields_ = [("string", ctypes.c_void_p), ("flag", ctypes.c_int),
                ("int64", ctypes.c_int64), ("double", ctypes.c_double),
                ("list", ctypes.c_void_p), ("byte_array", ctypes.c_void_p)]


class Node(ctypes.Structure):
    pass


class NodeList(ctypes.Structure):
    _fields_ = [("num", ctypes.c_int), ("values", ctypes.POINTER(Node)),
                ("keys", ctypes.POINTER(ctypes.c_char_p))]


Node._fields_ = [("u", NodeUnion), ("format", ctypes.c_int)]


class EventProperty(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char_p), ("format", ctypes.c_int),
                ("data", ctypes.c_void_p)]


class Event(ctypes.Structure):
    _fields_ = [("event_id", ctypes.c_int), ("error", ctypes.c_int),
                ("reply_user_data", ctypes.c_void_p), ("data", ctypes.c_void_p)]


class LogMessage(ctypes.Structure):
    _fields_ = [("prefix", ctypes.c_char_p), ("level", ctypes.c_char_p),
                ("text", ctypes.c_char_p), ("size", ctypes.c_int)]


class RenderParam(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("data", ctypes.c_void_p)]


class GLInit(ctypes.Structure):
    _fields_ = [("get_proc_address", ctypes.c_void_p), ("get_proc_address_ctx", ctypes.c_void_p)]


class GLFbo(ctypes.Structure):
    _fields_ = [("fbo", ctypes.c_int), ("w", ctypes.c_int),
                ("h", ctypes.c_int), ("internal_format", ctypes.c_int)]


GET_PROC = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p)
UPDATE_CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p)


def libmpv_path(config: cfg.Config | None = None) -> str:
    """优先用配置指定的 libmpv，其次 brew 的，最后 .dylib 软链。"""
    if config is not None:
        wanted = str(config.get("player.libmpv_path", "") or "")
        if wanted and Path(wanted).is_file():
            return wanted
    for candidate in ("/opt/homebrew/lib/libmpv.2.dylib", "/usr/local/lib/libmpv.2.dylib",
                      "/opt/homebrew/lib/libmpv.dylib"):
        if Path(candidate).is_file():
            return candidate
    raise RuntimeError("找不到 libmpv.2.dylib，先 brew install mpv")


def load_lib(config: cfg.Config | None = None) -> ctypes.CDLL:
    global _LIB
    with _OWN:
        if _LIB is not None:
            return _LIB
        # use_errno：mpv_create 只会「返回空」，但它内部 pthread_create/malloc 失败时
        # errno 是留得下来的 —— 没有这个数，每次失败都只能靠猜
        lib = ctypes.CDLL(libmpv_path(config), use_errno=True)
        lib.mpv_create.restype = ctypes.c_void_p
        lib.mpv_client_api_version.restype = ctypes.c_uint
        lib.mpv_initialize.argtypes = [ctypes.c_void_p]
        lib.mpv_set_option_string.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
        lib.mpv_set_property_string.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
        lib.mpv_get_property.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int,
                                         ctypes.c_void_p]
        lib.mpv_get_property_string.restype = ctypes.c_char_p
        lib.mpv_get_property_string.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        lib.mpv_command.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_char_p)]
        lib.mpv_observe_property.argtypes = [ctypes.c_void_p, ctypes.c_uint64, ctypes.c_char_p,
                                            ctypes.c_int]
        lib.mpv_wait_event.restype = ctypes.POINTER(Event)
        lib.mpv_wait_event.argtypes = [ctypes.c_void_p, ctypes.c_double]
        lib.mpv_free.argtypes = [ctypes.c_void_p]
        lib.mpv_free_node_contents.argtypes = [ctypes.POINTER(Node)]
        lib.mpv_terminate_destroy.argtypes = [ctypes.c_void_p]
        lib.mpv_request_event.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        # 没声明 argtypes 时 ctypes 会把 handle（Python int 形式的 64 位指针）按 C int 传，
        # 截断成 32 位直接段错误 —— 每个用到的符号都必须显式声明。
        lib.mpv_request_log_messages.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        lib.mpv_render_context_create.argtypes = [ctypes.POINTER(ctypes.c_void_p),
                                                 ctypes.c_void_p, ctypes.POINTER(RenderParam)]
        lib.mpv_render_context_render.argtypes = [ctypes.c_void_p, ctypes.POINTER(RenderParam)]
        lib.mpv_render_context_set_update_callback.argtypes = [ctypes.c_void_p, UPDATE_CB,
                                                              ctypes.c_void_p]
        lib.mpv_render_context_report_swap.argtypes = [ctypes.c_void_p]
        lib.mpv_render_context_free.argtypes = [ctypes.c_void_p]
        _LIB = lib
        return lib


def _decode(node: Node):
    fmt = node.format
    if fmt == F_STRING:
        if not node.u.string:
            return None
        return ctypes.cast(node.u.string, ctypes.c_char_p).value.decode("utf-8", "replace")
    if fmt == F_FLAG:
        return bool(node.u.flag)
    if fmt == F_INT64:
        return int(node.u.int64)
    if fmt == F_DOUBLE:
        return float(node.u.double)
    if fmt in (F_NODE_ARRAY, F_NODE_MAP):
        if not node.u.list:
            return [] if fmt == F_NODE_ARRAY else {}
        ptr = ctypes.cast(node.u.list, ctypes.POINTER(NodeList)).contents
        values = ptr.values
        if fmt == F_NODE_ARRAY:
            return [_decode(values[index]) for index in range(ptr.num)]
        out = {}
        for index in range(ptr.num):
            key = ptr.keys[index].decode("utf-8", "replace") if ptr.keys else str(index)
            out[key] = _decode(values[index])
        return out
    return None


LC_NUMERIC = 4        # macOS 的取值（Linux 是 1；1 在这台机器上是 LC_COLLATE，照抄会静默无效）


def force_c_numeric_locale() -> None:
    """mp_create() 的第一行就是 check_locale()：LC_NUMERIC 不是 "C" 就直接 return NULL。

    QApplication 初始化时会 setlocale(LC_ALL, "")，于是终端里带 LANG=zh_CN.UTF-8 启动的
    用户（双击 .command 的那条路）LC_NUMERIC 变成 zh_CN.UTF-8，mpv 判定失败 —— 而它那句
    解释只往 stderr 打印，GUI 里谁也看不见，表现就是「mpv_create 返回空、errno=0、
    线程也能建」。mpv 要求整个 core 生命周期都保持 C，所以这里改了不还原。
    """
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libSystem.B.dylib")
        libc.setlocale.restype = ctypes.c_char_p
        libc.setlocale(LC_NUMERIC, b"C")
    except Exception:
        pass


def numeric_locale() -> str:
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libSystem.B.dylib")
        libc.setlocale.restype = ctypes.c_char_p
        name = libc.setlocale(LC_NUMERIC, None)
        return (name or b"?").decode("utf-8", "replace")
    except Exception:
        return "?"


class LibmpvPlayer(QObject):
    """和 MpvController 同形的 in-process 播放器控制面。"""

    property_changed = Signal(str, object)
    tracks_changed = Signal(list)
    tick = Signal(object, object)
    mpv_log = Signal(str)
    died = Signal(int)
    connected = Signal()
    launch_failed = Signal(str)

    OBSERVED = ("time-pos", "duration", "pause", "idle-active", "eof-status", "track-list",
                "hwdec-current", "video-codec", "audio-codec", "media-title", "filename",
                "path", "chapter", "chapters", "volume", "mute", "speed", "sid", "aid",
                "secondary-sid", "sub-visibility", "playlist-count", "playlist-pos",
                "paused-for-cache", "demuxer-cache-state")

    def __init__(self, config: cfg.Config, parent=None):
        super().__init__(parent)
        self.config = config
        self.lib = None
        self.handle = None          # mpv_handle*
        self.render_ctx = None      # mpv_render_context*
        self.ipc_path = str(cfg.SUPPORT_DIR / f"mpv-sock-{os.getpid()}-{id(self):x}")
        self.permanent_error = False
        self._props: dict[str, object] = {}
        self._pump: threading.Thread | None = None
        self._stopping = False
        self._ready = threading.Event()
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(120)
        self._tick_timer.timeout.connect(self._poll_tick)
        self.log_tail: deque[str] = deque(maxlen=400)

    # ------------------------------------------------------------ 生命周期

    def launch(self, wid: int | None = None, extra=()) -> None:
        if self.handle:
            return
        cfg.ensure_dirs()
        self.permanent_error = False
        try:
            self.lib = load_lib(self.config)
        except Exception as exc:
            # 库都没有 = 装一下就好，不是重试能解决的
            self.permanent_error = True
            self.launch_failed.emit(f"加载 libmpv 失败：{exc}")
            return
        # 一次就够，别在这儿 sleep —— 主线程卡 0.7 秒不值得；重试交给 MainWindow 的定时器
        force_c_numeric_locale()
        self.handle = self.lib.mpv_create()
        last_errno = 0 if self.handle else ctypes.get_errno()
        if not self.handle:
            self.launch_failed.emit(self._failure_report(last_errno))
            return
        player = self.config.data["player"]
        sub = self.config.data["subtitle"]
        options = [
            ("vo", "libmpv"), ("config", "no"), ("terminal", "no"), ("msg-level", "all=v"),
            ("idle", "yes"), ("keep-open", "yes"), ("force-window", "no"),
            # 渲染由我们驱动、又不让 mpv 在 render() 里等显示节拍（见 video_widget 的
            # MPV_RENDER_PARAM_BLOCK_FOR_TARGET_TIME），此时 mpv 的 display-sync 时钟
            # 会认为「屏幕不刷新」而把播放停在 0.4s —— 必须让它按音频/墙钟自己走。
            ("video-sync", "desync"),
            # mpv 自带的 Lua 脚本（osc/stats/ytdl）会各自注册一个 client 和 hook，
            # 我们用自己的 UI，留着它们只会在销毁时留下「client removed during hook
            # handling」并触发 mp_clients_destroy 的 assert。
            ("load-scripts", "no"),
            ("sub-auto", "fuzzy"), ("hwdec", str(player["hwdec"])),
            ("volume", str(player["volume"])), ("speed", str(player["speed"])),
            ("sub-font", str(sub["font"])), ("sub-font-size", str(sub["size"])),
            ("sub-border-size", str(sub["outline"])), ("sub-shadow-offset", str(sub["shadow"])),
            ("sub-pos", str(sub["pos"])), ("sub-delay", str(sub["delay"])),
            ("sub-ass-override", "scale"),
        ]
        if sub.get("bold", True):
            options.append(("sub-bold", "yes"))
        if player.get("audio_passthrough"):
            options.append(("audio-spdif", "ac3,eac3,dts-hd,dts,truehd"))
        # 同形接口之外再开一个 IPC 套接字：验证脚本要用它独立取证，
        # 不能只信我们自己代码读到的属性。
        if self.ipc_path:
            options.append(("input-ipc-server", self.ipc_path))
        for key, value in options:
            rc = self.lib.mpv_set_option_string(self.handle, key.encode(), str(value).encode())
            if rc < 0:
                self.mpv_log.emit(f"选项 {key} 被拒（rc={rc}）")
        rc = self.lib.mpv_initialize(self.handle)
        if rc < 0:
            self.launch_failed.emit(f"mpv_initialize 失败 rc={rc}")
            return
        for name in self.OBSERVED:
            self.lib.mpv_observe_property(self.handle, 0, name.encode(), F_NODE)
        # libmpv 默认不投递日志；开了才知道 AO/VO 到底起了没（体检与验证都要这条证据）
        self.lib.mpv_request_log_messages(self.handle, b"info")
        self._stopping = False
        self._pump = threading.Thread(target=self._event_loop, daemon=True, name="mpv-events")
        self._pump.start()
        self._tick_timer.start()

    def _failure_report(self, errno_: int) -> str:
        """mpv_create 只给一个空指针，所以把能区分原因的几个量一次取出来 ——
        LC_NUMERIC 是真正咬过人的那一个，留在报告里免得下次再猜一轮。"""
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NPROC)
        api = self.lib.mpv_client_api_version()
        return (f"mpv_create 返回空（errno={errno_} "
                f"{ctypes.strerror(errno_) if errno_ else '未置位'} · "
                f"{Path(libmpv_path(self.config)).name} · API {api // 65536}.{api % 65536} · "
                f"LC_NUMERIC={numeric_locale()} · NPROC={soft}/{hard} "
                f"本进程线程={threading.active_count()}）")

    def _event_loop(self) -> None:
        first = True
        while not self._stopping:
            event = self.lib.mpv_wait_event(self.handle, 0.2)
            if not event:
                continue
            kind = event.contents.event_id
            if kind == EV_NONE:
                continue
            if kind == EV_PROPERTY_CHANGE and event.contents.data:
                prop = ctypes.cast(event.contents.data, ctypes.POINTER(EventProperty)).contents
                name = (prop.name or b"").decode("utf-8", "replace")
                value = None
                if prop.format == F_NODE and prop.data:
                    node = ctypes.cast(prop.data, ctypes.POINTER(Node)).contents
                    value = _decode(node)
                self._props[name] = value
                try:
                    if name == "track-list" and isinstance(value, list):
                        self.tracks_changed.emit(value)
                    self.property_changed.emit(name, value)
                except RuntimeError:      # 关窗后 QObject 已析构，尾巴事件直接丢
                    break
            elif kind == EV_LOG_MESSAGE and event.contents.data:
                msg = ctypes.cast(event.contents.data, ctypes.POINTER(LogMessage)).contents
                text = (msg.text or b"").decode("utf-8", "replace").strip()
                level = (msg.level or b"").decode("utf-8", "replace")
                prefix = (msg.prefix or b"").decode("utf-8", "replace")
                if text:
                    self.log_tail.append(f"[{level}] {prefix}: {text}")
                    # 只把 fatal 抛到状态栏：mpv 的 warn/error 里有一堆会自动恢复的
                    # （如 coreaudio 声道布局失败会退到 avfoundation），弹给用户只会吓人
                    if level == "fatal":
                        self.mpv_log.emit(f"{prefix}: {text}")
            elif kind == EV_FILE_LOADED:
                self.property_changed.emit("path", self.get("path"))
                self.property_changed.emit("filename", self.get("filename"))
            elif kind == EV_SHUTDOWN:
                break
            if first:
                first = False
                self._ready.set()
                self.connected.emit()
        self._ready.clear()

    # ------------------------------------------------------------ 协议原语

    def ready(self, timeout: float = 6.0) -> bool:
        return self._ready.wait(timeout)

    def command(self, *args, timeout: float = 8.0):
        if not self.handle:
            raise RuntimeError("mpv 未启动")
        argv = (ctypes.c_char_p * (len(args) + 1))()
        for index, item in enumerate(args):
            argv[index] = str(item).encode("utf-8")
        argv[len(args)] = None
        rc = self.lib.mpv_command(self.handle, argv)
        if rc < 0:
            raise RuntimeError(f"{args[0]} 失败 rc={rc}")
        return None

    def get(self, name: str, default=None, timeout: float = 2.0):
        if not self.handle:
            return self._props.get(name, default)
        node = Node()
        rc = self.lib.mpv_get_property(self.handle, name.encode(), F_NODE, ctypes.byref(node))
        if rc < 0:
            return self._props.get(name, default)
        try:
            value = _decode(node)
        finally:
            self.lib.mpv_free_node_contents(ctypes.byref(node))
        if value is None and default is not None:
            return default
        return value

    def set(self, name: str, value) -> None:
        if not self.handle:
            return
        if isinstance(value, bool):
            text = "yes" if value else "no"
        elif value is None:
            text = ""
        else:
            text = str(value)
        rc = self.lib.mpv_set_property_string(self.handle, name.encode(), text.encode())
        if rc < 0:
            self.mpv_log.emit(f"set {name}={text} 失败 rc={rc}")

    def poll_position(self, timeout: float = 0.6):
        position = self.get("time-pos")
        duration = self.get("duration")
        if position is not None:
            self._props["time-pos"] = position
        if duration is not None:
            self._props["duration"] = duration
        return self._props.get("time-pos"), self._props.get("duration")

    def _poll_tick(self) -> None:
        if not self.handle or not self._ready.is_set():
            return
        position, duration = self.poll_position()
        self.tick.emit(position, duration)

    # ------------------------------------------------------------ 与 MpvController 同形

    def is_alive(self) -> bool:
        return bool(self.handle) and not self._stopping

    def _apply_read_ahead(self, mb) -> None:
        """走单流代理时把 mpv 的预读压小。

        mpv 默认往前冲上百 MB 填 demuxer 缓存，会把 ASR 所在的播放头挤出代理那点
        内存缓存，两条读流互抢同一条上游 → 重置风暴 → 代理自保暂停 → 实时翻译停摆。
        """
        names = ("demuxer-max-bytes", "demuxer-max-back-bytes", "demuxer-readahead-secs")
        if not hasattr(self, "_demuxer_defaults"):
            try:
                self._demuxer_defaults = {name: self.get(name, default=None) for name in names}
            except Exception:
                self._demuxer_defaults = {}
        if mb:
            want = {"demuxer-max-bytes": f"{max(4, int(mb))}MiB",
                    "demuxer-max-back-bytes": "8MiB",
                    "demuxer-readahead-secs": "5"}
        else:
            want = {name: value for name, value in self._demuxer_defaults.items()
                    if value is not None}
        for name, value in want.items():
            self.set(name, value)

    def open_media(self, path: str, append: bool = False,
                   headers: dict[str, str] | None = None,
                   start: float | None = None, read_ahead_mb=None) -> None:
        fields = ", ".join(f"{name}: {value}" for name, value in (headers or {}).items())
        try:
            self.set("http-header-fields", fields)
        except Exception:
            pass
        if not append:
            # start 是 mpv 的 file option：必须在 loadfile 前设；
            # 重置要等文件真正加载后由调用方做，loadfile 是异步的。
            try:
                self.set("start", float(start) if start and start > 0 else 0.0)
            except Exception:
                pass
            self._apply_read_ahead(read_ahead_mb)
        self.command("loadfile", str(path), "append" if append else "replace")
        if not append:
            self.set("pause", False)

    def playlist_add(self, paths) -> None:
        for path in paths:
            self.command("loadfile", str(path), "append")

    def next_track(self) -> None:
        self.command("playlist-next")

    def prev_track(self) -> None:
        pos = self.get("playlist-pos", 0) or 0
        if (self.get("time-pos", 0.0) or 0.0) > 8.0 or pos <= 0:
            self.command("seek", 0, "absolute", "keyframes")
        else:
            self.command("playlist-prev")

    def set_paused(self, paused: bool) -> None:
        self.set("pause", bool(paused))

    def toggle_pause(self) -> None:
        self.set("pause", not bool(self.get("pause", False)))

    def seek(self, seconds: float, absolute: bool = False) -> None:
        self.command("seek", float(seconds), "absolute" if absolute else "relative", "exact")

    def jump(self, delta: float) -> None:
        self.command("seek", float(delta), "relative", "exact")

    def seek_percent(self, percent: float) -> None:
        self.set("percent-pos", float(percent))

    def frame_step(self, forward: bool = True) -> None:
        self.set("pause", True)
        self.command("frame-step" if forward else "frame-back-step")

    def set_speed(self, value: float) -> None:
        self.set("speed", float(value))

    def set_volume(self, value: float) -> None:
        self.set("volume", max(0.0, min(200.0, float(value))))

    def set_mute(self, muted: bool) -> None:
        self.set("mute", bool(muted))

    def set_hwdec(self, mode: str) -> str:
        self.set("hwdec", mode)
        self.config.set("player.hwdec", mode)
        return mode

    def hwdec_actual(self) -> str:
        return str(self.get("hwdec-current") or self._props.get("hwdec-current") or "")

    def tracks(self) -> list[dict]:
        value = self.get("track-list", []) or []
        return value if isinstance(value, list) else []

    def tracks_of(self, kind: str) -> list[dict]:
        return [t for t in self.tracks() if t.get("type") == kind]

    def select_track(self, kind: str, track_id) -> None:
        prop = {"audio": "aid", "sub": "sid", "video": "vid"}.get(kind, kind)
        self.set(prop, track_id if track_id is not None else "no")

    def set_secondary_sub(self, track_id) -> None:
        self.set("secondary-sid", track_id if track_id is not None else "no")

    def set_lang_prefs(self, audio_langs, sub_langs) -> None:
        from . import langtags
        self.set("alang", langtags.expand_for_mpv(list(audio_langs or [])))
        self.set("slang", langtags.expand_for_mpv(list(sub_langs or [])))

    def add_subtitle(self, path: str, select: bool = True):
        before = {t.get("id") for t in self.tracks_of("sub")}
        self.command("sub-add", str(path))
        pid = None
        deadline = time.time() + 3.0
        while time.time() < deadline:
            fresh = [t for t in self.tracks_of("sub") if t.get("id") not in before]
            if fresh:
                pid = fresh[-1].get("id")
                break
            time.sleep(0.1)
        if pid is None:
            chosen = [t for t in self.tracks_of("sub") if t.get("selected")]
            pid = chosen[-1].get("id") if chosen else None
        if select and pid is not None:
            self.set("sid", pid)
        return pid

    def remove_subtitle(self, track_id=None) -> None:
        self.command("sub-remove", *([track_id] if track_id is not None else []))

    def reload_subtitle(self) -> None:
        self.command("sub-reload")

    def set_sub_visible(self, visible: bool) -> None:
        self.set("sub-visibility", bool(visible))

    def apply_subtitle_style(self) -> None:
        sub = self.config.data["subtitle"]
        for key, prop in (("font", "sub-font"), ("size", "sub-font-size"),
                          ("outline", "sub-border-size"), ("shadow", "sub-shadow-offset"),
                          ("pos", "sub-pos"), ("delay", "sub-delay")):
            self.set(prop, sub[key])

    def set_chapter(self, index: int) -> None:
        self.set("chapter", int(index))

    def pin_to_rect(self, *args, **kwargs) -> None:
        """内嵌模式下画面就在我们控件里，没有独立窗口可贴。"""

    @property
    def position(self):
        value = self._props.get("time-pos")
        return float(value) if isinstance(value, (int, float)) else None

    @property
    def duration(self):
        value = self._props.get("duration")
        if value is None:
            value = self.get("duration")
        return float(value) if isinstance(value, (int, float)) else None

    def quit(self) -> None:
        if not self.handle:
            return
        self._stopping = True
        self._tick_timer.stop()
        try:
            self.command("quit")
        except Exception:
            pass
        # terminate_destroy 会等所有引用释放，但事件线程此刻还在 handle 里阻塞，
        # 必须先把泵收干净，否则就是 use-after-free
        pump, self._pump = self._pump, None
        if pump is not None:
            pump.join(3.0)
        try:
            self.lib.mpv_terminate_destroy(self.handle)
        except Exception:
            pass
        self.handle = None
        self._ready.clear()
        Path(self.ipc_path).unlink(missing_ok=True)
