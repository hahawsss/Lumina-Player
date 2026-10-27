"""mpv 引擎驱动：把 mpv 当子进程拉起来，用 JSON IPC（unix socket）控制。

解码、硬解、轨道管理全部由 mpv 负责；这里只做协议封装与常用操作，
好处是绑定关系极薄——mpv 升级不会像 Python 绑定库那样直接崩在 ABI 上。
"""
from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from . import config as cfg, i18n

_OBSERVED = (
    "time-pos", "pause", "idle-active", "eof-status", "track-list", "hwdec-current",
    "hwdec-controls", "video-codec", "audio-codec", "media-title", "filename", "chapter",
    "chapters", "edition-list", "playlist-count", "playlist-pos", "volume", "mute",
    "speed", "sid", "aid", "secondary-sid", "sub-visibility", "demuxer-cache-state",
    "paused-for-cache", "video-out-params/aspect", "path",
)

_POLL_PROPS = ("time-pos", "duration")


class MpvError(RuntimeError):
    pass


def find_mpv_binary(config: cfg.Config | None = None) -> str:
    """配置里可以显式指定 mpv 路径（比如自带渲染后端的分支构建），否则用 brew/PATH 上的。"""
    if config is not None:
        wanted = str(config.get("player.mpv_path", "") or "")
        if wanted:
            custom = Path(wanted).expanduser()
            if custom.is_file() and os.access(custom, os.X_OK):
                return str(custom)
    which = shutil.which("mpv")
    candidates = [which] if which else []
    candidates += [
        "/opt/homebrew/bin/mpv",
        "/usr/local/bin/mpv",
        "/Applications/mpv.app/Contents/MacOS/mpv",
    ]
    for path in candidates:
        if path and Path(path).is_file() and os.access(path, os.X_OK):
            return path
    raise MpvError(i18n.t("没找到 mpv 可执行文件，先装一个：brew install mpv"))


def _supported_options(binary: str) -> set[str]:
    """mpv 版本差异会让未知选项直接退出，所以发参数前先过一遍白名单。"""
    try:
        proc = subprocess.run(
            [binary, "--no-config", "--list-options"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    names: set[str] = set()
    for line in (proc.stdout + proc.stderr).splitlines():
        stripped = line.strip()
        if not stripped.startswith("--"):
            continue
        # 行形态是「--名字   类型 (default: …)」，先按空白切掉后半截
        token = stripped[2:].split()[0].split("=")[0]
        names.add(token)
        if token.startswith("no-"):
            names.add(token[3:])
    return names


def _supported_values(binary: str, option: str) -> set[str]:
    """`mpv --gpu-context=help` 列出该构建真正认的值。

    行形态是「名字 + 一列说明」，按空白切第一个 token；解析不出来时返回空集合，
    调用方必须当成「不可信」处理——传错值 mpv 是 fatal 退出，不是忽略。
    """
    try:
        proc = subprocess.run([binary, "--no-config", f"--{option}=help"],
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return set()
    names: set[str] = set()
    for line in (proc.stdout + proc.stderr).splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower().startswith("available"):
            continue
        token = stripped.split()[0].rstrip(":")
        if re.fullmatch(r"[a-z][a-z0-9_-]*", token):
            names.add(token)
    return names


_CTX_CACHE: dict[str, set[str]] = {}


def supported_gpu_contexts(binary: str) -> set[str]:
    key = str(binary)
    hit = _CTX_CACHE.get(key)
    if hit is None:
        hit = _supported_values(key, "gpu-context")
        _CTX_CACHE[key] = hit
    return hit


def gpu_apis(binary: str) -> set[str]:
    return _supported_values(str(binary), "gpu-api")


def can_embed(binary: str) -> bool:
    """这个 mpv 能不能用 --wid 把画面画进我们的 NSView。

    实测结论（macOS 27 + mpv 0.40/0.41）：**都不能**。上游已经删掉了 macOS 的
    OpenGL/cocoa GPU 上下文（0.40 源码里连 context_cocoa.m 都没有），剩下的
    Vulkan 上下文只认自己创建的窗口，传 --wid 会被忽略并自建一个窗口；
    自己编译一个「能内嵌」的 mpv 也做不到。真要单窗口只能走 libmpv 渲染 API
    自己画（IINA 的做法），那是另一个量级的工程。

    所以这里只做一件事：识别出「只有 Vulkan 上下文」的构建，让上层改用贴合窗口
    的方案，而不是白传一个被忽略的 --wid。
    """
    apis = gpu_apis(binary)
    if not apis:
        return False
    return "opengl" in apis       # 目前没有任何构建满足；保留给未来上游回归


def sweep_stale_sockets(keep: str = "") -> int:
    """删掉没人监听的旧 socket 文件。路径重名会让新 mpv 建不起 IPC 服务而直接退出。"""
    removed = 0
    for path in cfg.SUPPORT_DIR.glob("mpv-sock-*"):
        if str(path) == keep:
            continue
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(0.2)
        try:
            probe.connect(str(path))
        except OSError:
            pass                      # 连不上 = 没人监听 = 陈旧
        else:
            probe.close()
            continue
        probe.close()
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed


class MpvController(QObject):
    property_changed = Signal(str, object)
    tracks_changed = Signal(list)
    tick = Signal(object, object)          # (position, duration)
    mpv_log = Signal(str)
    died = Signal(int)
    connected = Signal()
    launch_failed = Signal(str)

    def __init__(self, config: cfg.Config, parent=None):
        super().__init__(parent)
        self.config = config
        self.binary = ""
        self.socket_path = ""
        self.proc: subprocess.Popen | None = None
        self._sock: socket.socket | None = None
        self._seq = 0
        self._pending: dict[int, list] = {}
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._reader: threading.Thread | None = None
        self._props: dict[str, object] = {}
        self._connected = threading.Event()
        self._stopping = False
        self._opts: set[str] = set()
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(120)
        self._tick_timer.timeout.connect(self._poll_tick)
        self._spawning = False
        self._pinned = ""
        self._wid: int | None = None
        self._extra: tuple[str, ...] = ()

    # ------------------------------------------------------------ 启动/关闭

    def launch(self, wid: int | None = None, extra=()) -> None:
        """主线程只做「找二进制 + 定 socket 路径」。

        探测 mpv 支持的选项、拉起进程、等 socket 全部放后台线程：否则启动那一下会把
        事件循环堵住，表现就是鼠标转圈、界面要点一下才出来。
        """
        if self.proc is not None or self._spawning:
            return
        cfg.ensure_dirs()
        self.binary = find_mpv_binary(self.config)
        # 每个实例一条 socket：只按 PID 命名的话，同进程里的第二个控制器会和第一个
        # 抢同一路径，新 mpv 建不起 IPC 服务就直接退出。
        self.socket_path = str(Path(cfg.SUPPORT_DIR) / f"mpv-sock-{os.getpid()}-{id(self):x}")
        sweep_stale_sockets(keep=self.socket_path)
        try:
            Path(self.socket_path).unlink(missing_ok=True)
        except OSError:
            pass
        self._wid, self._extra = wid, tuple(a for a in extra if a)
        self._spawning = True
        threading.Thread(target=self._spawn, daemon=True, name="mpv-spawn").start()
        self._tick_timer.start()

    def _build_args(self) -> list[str]:
        player = self.config.data["player"]
        sub = self.config.data["subtitle"]
        # --wid 内嵌在 macOS 上只有 vo=gpu(cocoa-cb) 支持；mpv 0.41 默认 gpu-next 会选
        # MoltenVK(macvk)，那条路径只能自建窗口，于是桌面上会多出一个 mpv 窗口。
        vo = str(player.get("vo", "gpu") or "gpu")
        args = [self.binary, "--no-config", "--idle=yes", "--keep-open=yes",
                "--no-osc", "--no-osd-bar", "--osd-level=0", "--really-quiet",
                f"--log-file={cfg.LOG_PATH}",
                f"--vo={vo}",
                "--no-resume-playback", "--force-window=yes", "--keepaspect=yes",
                "--sub-auto=fuzzy", "--icc-profile-auto=yes", "--audio-display=no",
                f"--hwdec={player['hwdec']}",
                f"--volume={player['volume']}",
                f"--speed={player['speed']}",
                f"--sub-font={sub['font']}",
                f"--sub-font-size={sub['size']}",
                f"--sub-border-size={sub['outline']}",
                f"--sub-shadow-offset={sub['shadow']}",
                f"--sub-pos={sub['pos']}",
                f"--sub-delay={sub['delay']}",
                f"--screenshot-directory={cfg.WORK_DIR / 'screenshots'}",
                "--screenshot-format=png",
                f"--input-ipc-server={self.socket_path}",
                "--sub-ass-override=scale"]
        embed = bool(player.get("embed_mpv", True)) and bool(self._wid)
        if vo != "null":
            allowed = supported_gpu_contexts(self.binary)
            context = str(player.get("gpu_context", "") or "")
            # 只在「确实解析出了可选值」且用户指定的值在里面时才传。
            # 拿不准就不传——传错值 mpv 是 fatal 退出，而不是忽略。
            if context and allowed and context in allowed:
                args.append(f"--gpu-context={context}")
        if player.get("audio_passthrough"):
            args.append("--audio-spdif=ac3,eac3,dts-hd,dts,truehd")
        if sub.get("bold", True):
            args.append("--sub-bold=yes")
        if player.get("embed_mpv", True) and self._wid:
            args.append(f"--wid={self._wid}")
        elif player.get("pin_window", True):
            # 内嵌不可用时退一步：让 mpv 的播放窗无边框、由我们把我们窗口的视频区
            # 尺寸贴上去，视觉上仍然是一个窗口（几何在 pin_to_rect 里持续同步）。
            args += ["--no-border", "--title=Lumina", "--no-keepaspect-window"]
        args += list(self._extra)
        args = [a for a in args if self._option_ok(a)]
        if not any(a.startswith("--input-ipc-server") for a in args):
            raise MpvError(i18n.t("这个 mpv 构建没有 --input-ipc-server，无法控制；请确认 brew install mpv 完成"))
        return args

    def _spawn(self) -> None:
        try:
            self._opts = _supported_options(self.binary)
            args = self._build_args()
            env = dict(os.environ)
            env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + env.get("PATH", "")
            self.proc = subprocess.Popen(
                args, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                env=env, cwd=str(cfg.WORK_DIR), text=True,
            )
        except BaseException as exc:
            self._spawning = False
            self.launch_failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self._spawning = False
        atexit.register(self._kill_child)
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        self._connect_socket()

    def _option_ok(self, arg: str) -> bool:
        if not arg.startswith("--"):
            return True
        name = arg[2:].split("=")[0]
        if not self._opts:
            return True
        # 布尔项可以写 --no-xxx，此时正主是 xxx
        return name in self._opts or (name.startswith("no-") and name[3:] in self._opts)

    def _drain_stderr(self) -> None:
        if not self.proc or not self.proc.stderr:
            return
        for line in self.proc.stderr:
            line = line.rstrip()
            if line:
                self.mpv_log.emit(line)
                if "unknown option" in line or "error" in line.lower():
                    print(f"[mpv] {line}", flush=True)

    def _connect_socket(self) -> None:
        deadline = time.time() + 8.0
        while time.time() < deadline:
            if self.proc and self.proc.poll() is not None:
                self.died.emit(self.proc.returncode)
                return
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(self.socket_path)
                sock.settimeout(1.0)
            except OSError:
                time.sleep(0.05)
                continue
            self._sock = sock
            self._connected.set()
            self._reader = threading.Thread(target=self._reader_loop, daemon=True)
            self._reader.start()
            for index, name in enumerate(_OBSERVED):
                self._send({"command": ["observe_property", index + 1, name]})
            self.connected.emit()
            return
        self.died.emit(-1)

    def _reader_loop(self) -> None:
        buf = b""
        sock = self._sock
        while sock is not None and not self._stopping:
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line.strip():
                    self._dispatch(line)
        self._connected.clear()

    def _dispatch(self, raw: bytes) -> None:
        try:
            msg = json.loads(raw.decode("utf-8", "replace"))
        except (ValueError, UnicodeDecodeError):
            return
        rid = msg.get("request_id")
        if rid is not None:
            with self._lock:
                slot = self._pending.get(rid)
            if slot is not None:
                slot[0] = msg
                slot[1].set()
                return
        event = msg.get("event")
        if event == "property-change":
            name, value = msg.get("name", ""), msg.get("data")
            self._props[name] = value
            if name == "track-list" and isinstance(value, list):
                self.tracks_changed.emit(value)
            self.property_changed.emit(name, value)

    # ------------------------------------------------------------ 协议原语

    def _send(self, payload: dict) -> None:
        sock = self._sock
        if sock is None:
            raise MpvError(i18n.t("mpv 还没连上"))
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        with self._write_lock:
            sock.sendall(data)

    def call(self, command: list, timeout: float = 6.0):
        if self.proc is None and not self._spawning:
            raise MpvError(i18n.t("mpv 还没启动"))
        if not self._connected.wait(min(2.0, timeout)):
            raise MpvError(i18n.t("mpv 未就绪"))
        with self._lock:
            self._seq += 1
            rid = self._seq
            slot = [None, threading.Event()]
            self._pending[rid] = slot
        self._send({"command": command, "request_id": rid})
        if not slot[1].wait(timeout):
            with self._lock:
                self._pending.pop(rid, None)
            raise MpvError(i18n.t("mpv 超时未响应：{}", command[0]))
        with self._lock:
            self._pending.pop(rid, None)
        reply = slot[0] or {}
        if reply.get("error") not in (None, "success"):
            raise MpvError(i18n.t("{} 失败：{}", command[0], reply.get("error")))
        return reply.get("data")

    def get(self, name: str, default=None, timeout: float = 2.0):
        # 没连上时直接回缓存：否则启动阶段每次读属性都要空等 2 秒超时，
        # 面板构造叠起来就是「点开应用鼠标转圈好几秒」。
        if not self._connected.is_set():
            return self._props.get(name, default)
        try:
            value = self.call(["get_property", name], timeout=timeout)
        except MpvError:
            return self._props.get(name, default)
        return default if value is None and default is not None else value

    def set(self, name: str, value) -> None:
        try:
            self.call(["set_property", name, value])
        except MpvError as exc:
            self.mpv_log.emit(f"set {name} 失败: {exc}")

    def command(self, *args, timeout: float = 8.0):
        return self.call(list(args), timeout=timeout)

    def get_many(self, names, timeout: float = 1.0) -> dict:
        """一次发多个请求、共享一个截止时间；轮询用，省掉串行往返。"""
        if not self._connected.wait(0.2):
            return {}
        slots: dict[str, list] = {}
        deadline = time.time() + timeout
        with self._lock:
            for name in names:
                self._seq += 1
                rid = self._seq
                slot = [None, threading.Event()]
                self._pending[rid] = slot
                slots[name] = [rid, slot]
        for name, (rid, _) in slots.items():
            self._send({"command": ["get_property", name], "request_id": rid})
        out: dict[str, object] = {}
        for name, (rid, slot) in slots.items():
            remaining = max(0.0, deadline - time.time())
            if slot[1].wait(remaining):
                out[name] = (slot[0] or {}).get("data")
            with self._lock:
                self._pending.pop(rid, None)
        return out

    def poll_position(self, timeout: float = 0.6) -> tuple[object, object]:
        data = self.get_many(("time-pos", "duration"), timeout=timeout)
        if "time-pos" in data:
            self._props["time-pos"] = data["time-pos"]
        if "duration" in data:
            self._props["duration"] = data["duration"]
        return self._props.get("time-pos"), self._props.get("duration")

    def _poll_tick(self) -> None:
        if not self._connected.is_set():
            return
        position, duration = self.poll_position()
        self.tick.emit(position, duration)

    # ------------------------------------------------------------ 高层操作

    def _apply_read_ahead(self, mb) -> None:
        """走单流代理时压小 mpv 的 demuxer 预读，别把 ASR 的播放头挤出代理缓存。"""
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
        mode = "append" if append else "replace"
        fields = ", ".join(f"{name}: {value}" for name, value in (headers or {}).items())
        # mpv 的 HTTP header 是全局选项；换回本地文件时必须清掉，否则会带旧 token
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
        self.command("loadfile", str(path), mode)
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
        style = "absolute" if absolute else "relative"
        try:
            self.command("seek", float(seconds), style, "exact")
        except MpvError:
            pass

    def seek_percent(self, percent: float) -> None:
        self.set("percent-pos", float(percent))

    def jump(self, delta: float) -> None:
        try:
            self.command("seek", float(delta), "relative", "exact")
        except MpvError:
            pass

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

    # --- 轨道 ---

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
        """mpv 0.41 的 sub-add 不回轨号（data 为 null），只能事后比对 track-list。"""
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

    def pin_to_rect(self, x: int, y: int, width: int, height: int, force: bool = False) -> None:
        """把 mpv 的播放窗贴到我们窗口的视频区（内嵌不可用时的替代方案）。"""
        if not self.is_alive() or width <= 0 or height <= 0:
            return
        spec = f"{int(width)}x{int(height)}+{int(x)}+{int(y)}"
        if spec == self._pinned and not force:
            return
        self._pinned = spec
        try:
            self.call(["set_property", "geometry", spec], timeout=1.0)
        except MpvError:
            pass

    def screenshot(self) -> None:
        self.command("screenshot", "video")

    def set_chapter(self, index: int) -> None:
        self.set("chapter", int(index))

    def set_edition(self, index: int) -> None:
        self.set("edition", int(index))

    # --- 生命周期 ---

    def _kill_child(self) -> None:
        self._stopping = True
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except OSError:
                pass
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass

    def quit(self) -> None:
        if self.proc is None:
            return
        try:
            self.command("quit")
        except (MpvError, OSError):
            pass
        for _ in range(30):
            if self.proc.poll() is not None:
                break
            time.sleep(0.05)
        self._kill_child()
        try:
            Path(self.socket_path).unlink(missing_ok=True)
        except OSError:
            pass

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def position(self) -> float | None:
        value = self._props.get("time-pos")
        return float(value) if isinstance(value, (int, float)) else None

    @property
    def duration(self) -> float | None:
        value = self._props.get("duration")
        if value is None:
            value = self.get("duration")
        return float(value) if isinstance(value, (int, float)) else None
