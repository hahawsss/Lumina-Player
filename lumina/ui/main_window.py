"""主窗口：装配 mpv 引擎、字幕管线、浮层控制条与侧栏。"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QMessageBox,
                               QPushButton, QSizePolicy, QVBoxLayout, QWidget,
                               QMainWindow)

from . import theme
from .controls import VideoOverlay
from .panels import Sidebar, track_row, PAGE_INDEX, CLOUD_SUBPAGE
from .. import asr, cloud, config as cfg, i18n, langtags, naming, translate
from .. import __version__
from ..engine import MpvController, MpvError
from ..media import MediaSource
from ..pipeline import SubtitlePipeline
from ..stream_proxy import RangeCacheProxy

MODE_LABELS = {"original": "只看原文", "bilingual": "双语", "translated": "只看译文"}

def subtitle_filter() -> str:
    return i18n.t("字幕文件 (*.srt *.ass *.ssa *.vtt *.sub)")


class MainWindow(QMainWindow):
    cloud_proxy_ready = Signal(object)
    cloud_proxy_failed = Signal(object)
    cloud_service_note = Signal(str)

    def __init__(self, config: cfg.Config, paths=None):
        super().__init__()
        self.config = config
        self.setWindowTitle("Lumina")
        self.setAcceptDrops(True)
        accent = config.get("ui.accent", "#4fa8ff")
        self.setMinimumSize(760, 460)

        central = QWidget(self)
        central.setObjectName("CentralArea")
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.backend = str(config.get("player.backend", "libmpv") or "libmpv")
        self.video_area = QWidget(central)
        self.video_area.setObjectName("VideoHost")
        self.video_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        area_layout = QHBoxLayout(self.video_area)
        area_layout.setContentsMargins(0, 0, 0, 0)
        area_layout.setSpacing(0)

        if self.backend == "libmpv":
            from ..mpv_lib import LibmpvPlayer
            from .video_widget import MpvVideoWidget
            self.engine = LibmpvPlayer(config, self)
            self.video_host = MpvVideoWidget(self.engine, self.video_area)
            area_layout.addWidget(self.video_host)
        else:
            self.engine = MpvController(config, self)
            self.video_host = self.video_area
        layout.addWidget(self.video_area, 1)

        self.pipeline = SubtitlePipeline(self.engine, config, self)
        self._pending_volume: float | None = None
        self._volume_save_timer = QTimer(self)
        self._volume_save_timer.setSingleShot(True)
        self._volume_save_timer.timeout.connect(self._flush_volume)
        self.cloud_client = cloud.OpenList(config)
        self.sidebar = Sidebar(config, self.engine, self.pipeline, self.cloud_client,
                               central)
        layout.addWidget(self.sidebar)
        self.sidebar.hide()

        host = self.video_area if self.backend == "libmpv" else None
        self.overlay = VideoOverlay(self, accent, host=host)
        if host is None and not config.get("player.embed_mpv", False):
            # ipc 后端下 mpv 是另一个进程的窗口，控制条必须浮在它之上才点得到
            self.overlay.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.overlay.bar.seek.set_colors(accent)
        self.welcome = self._build_welcome(self.overlay)
        self.overlay.show_welcome(self.welcome)
        # vo=libmpv 在没有渲染上下文时开片会让 mpv 判定「没有视频轨」且不可恢复，
        # 所以首开必须等 GL 上下文就绪（就绪后 _on_gl_ready 会补放排队的文件）
        if self.backend == "libmpv":
            self.video_host.context_ready.connect(self._on_gl_ready)

        self._current: str | None = None
        self._current_source: MediaSource | None = None
        self._pending_source: MediaSource | None = None
        self._arbitrating = False
        self._arb_source: MediaSource | None = None
        self._arb_position = 0.0
        self._arb_was_playing = False
        self._stream_proxy = None
        self._proxy_source: MediaSource | None = None
        self._cloud_live_state = None
        self._cloud_live_starting = False
        self._cloud_live_seq = 0
        self.cloud_proxy_ready.connect(self._on_cloud_proxy_ready)
        self.cloud_proxy_failed.connect(self._on_cloud_proxy_failed)
        self.cloud_service_note.connect(self._on_cloud_service_note)
        self._duration = 0.0
        self._paused = True
        self._engine_ready = False
        self._gl_ready = self.backend != "libmpv"
        self._queued: list[str] = []
        self._cache_deadline = 0.0
        self._buffered = 0.0
        self._track_debounce = QTimer(self)
        self._track_debounce.setSingleShot(True)
        self._track_debounce.setInterval(240)
        self._track_debounce.timeout.connect(self._reload_tracks)
        self._start_error: str | None = None
        self._engine_tries = 0
        self._closing = False

        self._wire_engine()
        self._wire_overlay()
        self._wire_panels()
        self._install_shortcuts()
        # libmpv 起来只要 1~40ms，直接在构造里同步拉起来 —— 窗口出现时引擎就已经就绪，
        # 不再有「引擎启动中」那一小段。子进程那条路要等 socket 连上，仍然延后，别挡首屏。
        if self.backend == "libmpv":
            self._start_engine()
        else:
            QTimer.singleShot(60, self._start_engine)
        # 网盘网关是软件的附属品：默认跟着软件起来（后台拉，别挡首屏）
        QTimer.singleShot(400, self._autostart_cloud)
        if paths:
            self.queue_paths(paths)
        i18n.bind(self, self._apply_texts)

    # ------------------------------------------------------------ 云盘网关

    def _autostart_cloud(self) -> None:
        """启动期把 OpenList 带起来；失败只说明一次，本地播放照旧。"""
        def worker() -> None:
            try:
                ok, message = self.cloud_client.autostart()
            except Exception as exc:
                # 后台线程里没人接的异常等于静默失败：网关没起来，界面一句提示都没有
                ok, message = False, i18n.t("OpenList 启动过程出错：{}: {}",
                                                type(exc).__name__, exc)
            cfg.append_log(message)
            if not ok:
                self.cloud_service_note.emit(message)
        threading.Thread(target=worker, daemon=True, name="openlist-autostart").start()

    def _on_cloud_service_note(self, text: str) -> None:
        self.overlay.show_osd(text + i18n.t("（网盘不可用，本地播放不受影响）"), 6000)

    def shutdown_cloud(self) -> None:
        """关窗 / 退出 / 收到信号都要走这里：网关是软件的附属品，别留孤儿进程。"""
        if getattr(self, "_cloud_stopped", False):
            return
        self._cloud_stopped = True
        try:
            self.cloud_client.shutdown_for_quit()
        except Exception as exc:                       # 退出路径不能因停机失败而卡住
            cfg.append_log(f"关闭 OpenList 失败：{exc}")

    # ------------------------------------------------------------ 首屏

    def _build_welcome(self, parent) -> QWidget:
        card = QWidget(parent)
        card.setObjectName("BarPanel")
        box = QVBoxLayout(card)
        box.setContentsMargins(30, 26, 30, 26)
        box.setSpacing(10)
        # 语言开关放在首屏右上角：第一次打开、还没认出界面语言时就能换
        lang_row = QHBoxLayout()
        lang_row.addStretch(1)
        self.welcome_language = QPushButton("English", card)
        self.welcome_language.setObjectName("LanguageChip")
        self.welcome_language.setCursor(Qt.PointingHandCursor)
        self.welcome_language.setFlat(True)
        self.welcome_language.clicked.connect(self.toggle_language)
        lang_row.addWidget(self.welcome_language)
        box.addLayout(lang_row)
        title = QLabel("Lumina Player", card)
        title.setObjectName("TitleText")
        title.setAlignment(Qt.AlignCenter)
        hint = QLabel("", card)
        hint.setObjectName("SubText")
        hint.setAlignment(Qt.AlignCenter)
        hint.setWordWrap(True)
        open_button = QPushButton("", card)
        theme.set_role(open_button, "primary")
        open_button.clicked.connect(self.open_file_dialog)
        self.welcome_hint = hint
        self.welcome_open = open_button
        box.addWidget(title)
        box.addWidget(hint)
        box.addWidget(open_button, 0, Qt.AlignCenter)
        from .panels import DoctorCard
        self.doctor = DoctorCard(self.config, card)
        self.doctor.hide()        # 后台体检回来后，只有真缺东西才显示
        self.doctor.checked.connect(self._sync_doctor)
        box.addWidget(self.doctor)
        card.setMinimumWidth(460)
        return card

    def _apply_texts(self) -> None:
        self.welcome_hint.setText(i18n.t("拖入视频，或打开文件 · 空格播放/暂停 · F 全屏 · T 生成 AI 字幕"))
        self.welcome_open.setText(i18n.t("打开视频…"))
        other = "English" if i18n.current() == "zh" else "中文"
        self.welcome_language.setText(other)
        self.welcome_language.setToolTip(
            "Switch to English" if other == "English" else "切换成中文")
        self._update_badge()      # 顶栏徽标是拼出来的（解码器/编码/音轨语言）

    def toggle_language(self) -> None:
        self.set_language("en" if i18n.current() == "zh" else "zh")

    def set_language(self, pref: str) -> None:
        self.config.set("ui.language", pref)
        i18n.set_language(i18n.resolve(pref))
        i18n.retranslate()
        self.sidebar.settings.sync_language()
        self.overlay.show_osd(
            "Language：English" if i18n.current() == "en" else "界面语言：中文", 1600)

    def _sync_doctor(self) -> None:
        """体检结果是异步回来的：卡片要先长高再重新居中，否则会把按钮压扁。"""
        self.doctor.setVisible(any(not item.ok for item in self.doctor.items))
        if self.welcome.isVisible():
            self.overlay.place_welcome()

    # ------------------------------------------------------------ 启动 mpv

    ENGINE_TRIES = 6            # libmpv 建 core 失败大多是瞬时的线程/内存压力，先自己重试
    ENGINE_RETRY_MS = 1500

    def _start_engine(self) -> None:
        wid = None
        # 只有 ipc 后端需要原生窗口手柄：libmpv 把帧画进我们自己的 GL 控件，不认 --wid
        if self.backend == "ipc" and self.config.get("player.embed_mpv", True):
            self.video_host.winId()                     # 逼出原生 NSView，再把手柄交给 mpv
            wid = int(self.video_host.winId())
        self._start_error = None
        try:
            self.engine.launch(wid=wid)
        except BaseException as exc:       # 含 mpv 侧任何意外，都要留下痕迹
            self._start_error = f"{type(exc).__name__}: {exc}"
        if self.backend == "ipc":
            # 子进程那条路是异步拉起的：launch() 返回时还没连上，
            # 成败只能等 connected / launch_failed，不能在这里判死
            self.overlay.poke()
            return
        if not self._start_error and self.engine.is_alive():
            if self._engine_tries:
                cfg.append_log(f"引擎在第 {self._engine_tries + 1} 次尝试起来了")
            self._engine_tries = 0
            self.overlay.poke()
            return
        self._engine_tries += 1
        if self._engine_tries <= 6 or self._engine_tries % 10 == 0:
            cfg.append_log(f"引擎没起来（第 {self._engine_tries} 次）{self._start_error or ''}")
        if self._engine_tries == self.ENGINE_TRIES:
            self._engine_complain()          # 先说一次，之后继续在后台试
        if not getattr(self.engine, "permanent_error", False) and not self._closing:
            # 无上限：这类失败是机器状态问题，用户不该重开来赌；起来了会自己恢复
            QTimer.singleShot(self.ENGINE_RETRY_MS if self._engine_tries < 10 else 3000,
                              self._start_engine)

    def _engine_complain(self) -> None:
        cfg.append_log(f"引擎暂时起不来 {self._start_error or ''} backend={self.backend}")
        # 不弹模态：首屏的自检卡片上就有「一键安装」，弹窗只会挡路
        self.overlay.show_osd(i18n.t("引擎暂时起不来，正在后台自动重试")
                              + (i18n.t("：{}", self._start_error)
                                 if self._engine_tries <= self.ENGINE_TRIES else ""), 9000)
        card = getattr(self, "doctor", None)
        if card is not None:
            card.refresh()
            card.show()

    def _on_gl_ready(self) -> None:
        self._gl_ready = True
        self._flush_queue()

    def _flush_queue(self) -> None:
        if not (self._engine_ready and self._gl_ready):
            return
        if self._queued:
            queued, self._queued = self._queued, []
            self.open_paths(queued)
            return
        if self._pending_source is not None:
            self._open_pending_source()

    def _on_engine_ready(self) -> None:
        """mpv 的 IPC 连上之后才能发指令；在此之前打开请求一律排队。"""
        self._engine_ready = True
        self.engine.set_lang_prefs(
            self.config.get("languages.default_audio_langs", []),
            self.config.get("languages.default_subtitle_langs", []))
        # 就绪这一刻必须补一次贴合：showEvent 时 mpv 还没连上，那次被跳过了
        self._pin_player_window()
        self._flush_queue()

    def queue_paths(self, paths) -> None:
        for path in paths:
            if path and path not in self._queued:
                self._queued.append(path)
        if self._engine_ready and self._gl_ready:
            self._flush_queue()
        else:
            self._start_engine()

    def _wire_engine(self) -> None:
        self.engine.property_changed.connect(self._on_property)
        self.engine.connected.connect(self._on_engine_ready)
        self.engine.tracks_changed.connect(lambda _list: self._track_debounce.start())
        self.engine.tick.connect(self._on_tick)
        self.engine.died.connect(self._on_mpv_died)
        self.engine.mpv_log.connect(lambda line: self.statusBar().showMessage(line[:120], 4000))
        self.engine.launch_failed.connect(self._on_launch_failed)

    def _on_launch_failed(self, text: str) -> None:
        # 只记录，不弹提示：瞬时压力（线程/内存）会在几次重试内自己好，
        # 真起不来的时候由 _engine_give_up 说清楚原因
        self._start_error = text
        if self.backend == "ipc":
            self._engine_complain()    # 那条路是异步的，这里就是它唯一的失败出口
        card = getattr(self, "doctor", None)
        if card is not None:
            card.refresh()
            card.show()

    def _wire_overlay(self) -> None:
        bar = self.overlay.bar
        bar.toggle_play.connect(self.toggle_play)
        bar.seek_absolute.connect(lambda value: self.engine.seek(value, absolute=True))
        bar.seek_delta.connect(lambda value: self.engine.jump(value))
        bar.speed_step.connect(self._bump_speed)
        bar.speed_picked.connect(self._set_speed)
        bar.volume_changed.connect(self._set_volume)
        bar.mute_toggled.connect(self._toggle_mute)
        bar.stop_clicked.connect(self._stop_playback)
        bar.fullscreen_toggled.connect(self.toggle_fullscreen)
        bar.next_item.connect(self.engine.next_track)
        bar.prev_item.connect(self.engine.prev_track)
        bar.open_tracks.connect(lambda: self.show_sidebar("tracks"))
        bar.open_subtitles.connect(lambda: self.show_sidebar("tracks"))
        bar.open_ai.connect(lambda: self.show_sidebar("ai"))
        bar.open_settings.connect(lambda: self.show_sidebar("settings"))
        bar.open_playlist.connect(lambda: self.show_sidebar("playlist"))
        bar.screenshot.connect(self._screenshot)
        self.sidebar.close_requested.connect(self.hide_sidebar)

    def _wire_panels(self) -> None:
        self.sidebar.language_toggle_requested.connect(self.toggle_language)
        self.pipeline.progress.connect(self._on_progress)
        self.pipeline.notice.connect(lambda text: self.overlay.show_osd(text, 2600))
        self.pipeline.error.connect(lambda text: self.overlay.show_osd("✗ " + text, 5000))
        self.pipeline.busy.connect(lambda value: self.overlay.set_badge(
            i18n.t("AI/翻译进行中") if value else ""))
        self.sidebar.tracks.add_subtitle_file.connect(self._add_subtitle_file)
        self.sidebar.tracks.subtitle_mode_changed.connect(
            lambda mode: self.overlay.show_osd(i18n.t(MODE_LABELS.get(mode, mode)), 1200))
        self.sidebar.ai.request.connect(self._request_ai)
        self.sidebar.ai.one_click_requested.connect(self._request_one_click)
        self.sidebar.ai.translate_requested.connect(self._translate_track)
        self.sidebar.cloud_browser.open_source.connect(self.open_media_source)
        self.sidebar.playlist.play_path.connect(self.open_path)
        self.pipeline.asr_finished.connect(self._on_asr_finished)
        self.pipeline.busy.connect(self._on_pipeline_busy)
        self.sidebar.settings.restart_needed.connect(
            lambda: self.overlay.show_osd(i18n.t("这项要重启播放器才生效"), 4000))

    def _install_shortcuts(self) -> None:
        def bind(sequence: str, handler) -> None:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.WindowShortcut)
            shortcut.activated.connect(handler)

        bind("Space", self.toggle_play)
        bind("K", self.toggle_play)
        bind("Left", lambda: self.engine.jump(-10))
        bind("Right", lambda: self.engine.jump(30))
        bind("Shift+Left", lambda: self.engine.jump(-60))
        bind("Shift+Right", lambda: self.engine.jump(60))
        bind("Alt+Left", self.engine.prev_track)
        bind("Alt+Right", self.engine.next_track)
        bind("Up", lambda: self._set_volume(int(self.config.get("player.volume", 100)) + 5))
        bind("Down", lambda: self._set_volume(int(self.config.get("player.volume", 100)) - 5))
        bind("M", self._toggle_mute)
        bind("[", lambda: self._bump_speed(-0.25))
        bind("]", lambda: self._bump_speed(0.25))
        bind("Backspace", lambda: self._set_speed(1.0))
        bind(",", lambda: self.engine.frame_step(False))
        bind(".", lambda: self.engine.frame_step(True))
        bind("F", self.toggle_fullscreen)
        bind("Escape", self._exit_fullscreen)
        bind("S", self._cycle_subtitle_track)
        bind("Z", self._cycle_display_mode)
        bind("A", self._cycle_audio_track)
        bind("T", lambda: self.show_sidebar("ai"))
        bind("L", lambda: self.show_sidebar("playlist"))
        bind("G", lambda: self.show_sidebar("tracks"))
        bind("I", lambda: self.show_sidebar("settings"))
        bind("D", lambda: self.show_sidebar("115"))
        bind("R", lambda: self.show_sidebar("storage"))
        bind("O", self.open_file_dialog)
        bind("Ctrl+O", self.open_file_dialog)
        bind("Ctrl+,", lambda: self.show_sidebar("settings"))
        bind("Ctrl+S", self._add_subtitle_file)
        bind("Ctrl+H", self.hide_sidebar)
        bind("Ctrl+Shift+D", self._debug_state)

    # ------------------------------------------------------------ 打开文件

    def open_file_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, i18n.t("打开视频"), str(Path.home()),
                                              cfg.media_filter())
        if path:
            self.open_path(path)

    def open_folder_dialog(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, i18n.t("打开文件夹"), str(Path.home()))
        if not folder:
            return
        found = [str(p) for p in sorted(Path(folder).rglob("*"))
                 if p.suffix.lower() in cfg.MEDIA_EXTS]
        if not found:
            self.overlay.show_osd(i18n.t("{} 里没有可播放的文件", folder), 3000)
            return
        self.sidebar.playlist.list.clear()
        for path in found:
            self.sidebar.playlist.append(path)
        self.open_paths(found)

    def refresh_recent_menu(self, menu) -> None:
        menu.clear()
        for path in self.config.recent[:12]:
            action = menu.addAction(Path(path).name)
            action.triggered.connect(lambda _=False, p=path: self.open_path(p))

    def show_doctor(self) -> None:
        self.show_sidebar("settings")
        self.overlay.show_osd(i18n.t("设置页底部＝环境自检与一键修复"), 2600)

    def show_about(self) -> None:
        models = ", ".join(asr.list_models()) or i18n.t("无")
        QMessageBox.information(
            self, i18n.t("关于 Lumina"),
            f"<b>Lumina Player {__version__}</b><br><br>"
            + i18n.t("解码引擎：mpv（VideoToolbox 硬解 / 软解可切）") + "<br>"
            + i18n.t("语音转字幕：whisper.cpp（Metal）") + "<br>"
            + i18n.t("引擎状态：{}", i18n.t("运行中") if self.engine.is_alive()
                     else i18n.t("未启动")) + "<br>"
            + i18n.t("转写模型：{}", models) + "<br>"
            + i18n.t("模型目录：{}", cfg.MODEL_DIR) + "<br>"
            + i18n.t("配置：{}", cfg.CONFIG_PATH) + "<br>"
            + i18n.t("日志：{}", cfg.APP_LOG_PATH))

    def _add_subtitle_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, i18n.t("添加字幕"), str(Path.home()),
                                              subtitle_filter())
        if not path:
            return
        pid = self.engine.add_subtitle(path, select=True)
        self.overlay.show_osd(i18n.t("已加载字幕 {}", Path(path).name), 2000)
        self.sidebar.tracks.reload()

    def open_paths(self, paths) -> None:
        paths = [p for p in paths if p]
        if not paths:
            return
        extras = paths[1:]
        self.open_path(paths[0])
        for path in extras:
            self.engine.playlist_add([path])
            self.sidebar.playlist.append(path)

    def open_path(self, path: str) -> None:
        resolved = str(Path(path).expanduser())
        if not Path(resolved).exists():
            self.overlay.show_osd(i18n.t("文件不存在：{}", Path(resolved).name), 3000)
            return
        self.open_media_source(MediaSource.local(resolved))

    def open_media_source(self, source: MediaSource) -> None:
        """本地文件和云端 WebDAV 都从这里进入；headers 只在这一次 loadfile 前设置。"""
        self._cloud_live_seq += 1
        self._cloud_live_starting = False
        self._cloud_live_state = None
        if self._proxy_source is not None:
            # 换片/重开都先撤掉单流代理，避免它和新的直连同时读 115
            self.pipeline.stop_all()
            self._stop_stream_proxy()
            # 同一个 115 key 重开时也要让 _media_loaded 重新绑定管线，
            # 否则管线还指着已经关掉的代理地址
            self._current = None
            self._current_source = None
        self._pending_source = source
        if not self._engine_ready or not self._gl_ready:
            self.overlay.show_osd(i18n.t("引擎启动中，稍等…"), 1800)
            self._start_engine()
            return
        self._open_pending_source()

    def _open_pending_source(self) -> None:
        source = self._pending_source
        if source is None:
            return
        self.engine.open_media(source.uri, headers=source.headers)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.toLocalFile()]
        media, subs = [], []
        for path in paths:
            if Path(path).suffix.lower() in cfg.SUB_EXTS:
                subs.append(path)
            else:
                media.append(path)
        if media:
            self.open_paths(media)
        for sub in subs:
            self.engine.add_subtitle(sub, select=True)

    # ------------------------------------------------------------ 状态回传

    def _on_property(self, name: str, value) -> None:
        if name == "pause":
            self._paused = bool(value) if value is not None else True
            self.overlay.bar.set_paused(self._paused)
            if self._paused:
                self.overlay.poke()
        elif name == "volume":
            if value is not None:
                self._remember_volume(float(value))
                self.overlay.bar.set_volume(int(float(value)), bool(self.engine.get("mute", False)))
        elif name == "mute":
            self.overlay.bar.set_volume(int(self.config.get("player.volume", 100)), bool(value))
        elif name == "speed":
            if value is not None:
                self.overlay.bar.set_speed(float(value))
                self.config.set("player.speed", float(value))
        elif name == "path" and value:
            # 只认 path（绝对路径）。filename 是裸文件名，两个都接的话，
            # filename 后到就把 pipeline 里的媒体路径覆盖成相对名，whisper 抽音频直接失败
            self._media_loaded(str(value))
        elif name == "hwdec-current":
            self._update_badge()
        elif name == "eof-status":
            if value == "eof":
                self.overlay.show_osd(i18n.t("播放结束"), 2500)

    def _media_loaded(self, path: str) -> None:
        source = self._pending_source
        if source is not None and source.matches(path):
            self._pending_source = None
        elif self._current_source is not None and self._current_source.matches(path):
            # libmpv 开文件后会再发一次 path（且 mpv 把 http:// 写成 http:/）；
            # 当成新素材处理会把云端 source 换成没有 header 的本地 source，
            # 播放没事，随后 ASR 却拿到 401。
            return
        elif source is not None:
            return          # 旧文件的尾巴事件，别抢走还没落地的 pending source
        else:
            source = MediaSource.local(path)
        if self._arbitrating and self._arb_source is not None \
                and source.key != self._arb_source.key:
            self._arbitrating = False
            self._arb_source = None
        if source.key == self._current:
            return
        self._current = source.key
        self._current_source = source
        if not source.is_remote and not source.uri.lower().startswith("http"):
            self.config.push_recent(source.uri)
        self.pipeline.begin_media(source)
        self.sidebar.ai.sync_language(self.pipeline.language_hint())
        self.sidebar.playlist.set_current(source.uri)
        self.sidebar.playlist.reload_recent()
        self.overlay.set_title(source.display_name)
        self.overlay.show_welcome(None)
        self._track_debounce.start()
        self._update_badge()
        QTimer.singleShot(350, lambda s=source: self._restore_generated(s))
        # mpv 载入新素材时会按视频尺寸重设自己的窗口，压过我们之前的贴合，所以补一次
        self._pin_player_window(force=True)
        QTimer.singleShot(220, lambda: self._pin_player_window(force=True))

    def _restore_generated(self, source: MediaSource | str) -> None:
        """上一轮已经生成过这部片子的字幕：打开就直接挂回来，不用再点一次。"""
        if isinstance(source, str):
            source = MediaSource.local(source)
        if source.key != self._current or not self.engine.is_alive():
            return
        if self.pipeline.restore_existing():
            self.sidebar.tracks.reload()
            self._update_badge()

    def _reload_tracks(self) -> None:
        if not self.engine.is_alive():
            return
        self.sidebar.tracks.reload()
        self.sidebar.ai._update_lang_note()
        self._update_badge()

    def _update_badge(self) -> None:
        if not self.engine.is_alive():
            self.overlay.set_badge("")
            return
        actual = self.engine.hwdec_actual()
        wanted = self.config.get("player.hwdec", "auto-safe")
        video = next((t.get("codec") for t in self.engine.tracks_of("video") if t.get("selected")), None)
        audio = next((t for t in self.engine.tracks_of("audio") if t.get("selected")), None)
        parts = [i18n.t("解：{}", actual or wanted)]
        if video:
            parts.append(str(video).upper())
        if audio:
            parts.append(f"{naming.lang_name(langtags.language_name(audio.get('lang')))}·"
                         f"{str(audio.get('codec') or '?').upper()}")
        self.overlay.set_badge("  ".join(parts))

    def _on_tick(self, position, duration) -> None:
        if duration:
            self._duration = float(duration)
        self.overlay.bar.set_time(position, self._duration or None)
        self.overlay.bar.seek.set_range(self._duration or None)
        buffered = 0.0
        if position is not None and self._duration:
            buffered = self._buffered
        self.overlay.bar.seek.set_position(position, buffered)
        now = time.time()
        if now > self._cache_deadline and self._duration:
            self._cache_deadline = now + 1.5
            state = self.engine.get("demuxer-cache-state", default=None, timeout=0.4)
            if isinstance(state, dict):
                end = state.get("cache-end")
                if isinstance(end, (int, float)):
                    self._buffered = float(end)

    def _on_progress(self, stage: str, done: int, total: int) -> None:
        self.sidebar.ai.set_progress(stage, done, total)
        if total and done and (done % 96 == 0):
            self.overlay.show_osd(f"{stage} {done}/{total}", 1200)

    def _on_mpv_died(self, code: int) -> None:
        self._stop_stream_proxy()
        self.overlay.show_osd(i18n.t("mpv 退出了（{}），重启播放器再试", code), 6000)

    # ------------------------------------------------------------ 动作

    def toggle_play(self) -> None:
        if not self.engine.is_alive():
            return
        if not self._current:
            # 用本地状态判断「有没有在放东西」：问 mpv 的 idle 一旦超时或取到选项值，
            # 播放中按空格就会弹出模态文件框，把整个界面钉住。
            self.open_file_dialog()
            return
        self.engine.toggle_pause()
        self.overlay.poke()

    def _remember_volume(self, value: float) -> None:
        """拖音量条会每像素改一次；先立刻设给 mpv，配置写盘合并到最后一次再落。"""
        self._pending_volume = value
        self._volume_save_timer.start(350)

    def _flush_volume(self) -> None:
        if self._pending_volume is not None:
            self.config.set("player.volume", self._pending_volume)
            self._pending_volume = None

    def _set_volume(self, value: int) -> None:
        value = max(0, min(160, int(value)))
        self.engine.set_volume(value)
        self._remember_volume(value)
        self.overlay.bar.set_volume(value, False)
        self.overlay.show_osd(i18n.t("音量 {}", value), 900)

    def _stop_playback(self) -> None:
        """停止 = 卸掉当前媒体并回到首屏，不是把画面冻在原地。"""
        if not self._current:
            return
        self._current = None
        self._current_source = None
        self._pending_source = None
        self._arbitrating = False
        self._arb_source = None
        self._cloud_live_state = None
        self._cloud_live_seq += 1
        self._cloud_live_starting = False
        self.pipeline.stop_all()
        self._stop_stream_proxy()
        self.pipeline.current_media = None
        self.pipeline.media_source = None
        self.pipeline.media_key = ""
        self.pipeline.media_stem = "lumina"
        self.pipeline.source = None
        self.pipeline.cues = []
        self.pipeline.translated = []
        try:
            self.engine.command("stop")
        except Exception:
            try:
                self.engine.set("pause", True)
            except Exception:
                pass
        self._paused = True
        self.overlay.bar.set_paused(True)
        self.overlay.bar.set_time(None, None)
        self.overlay.bar.seek.set_range(None)
        self.overlay.bar.seek.set_position(None)
        self.overlay.set_title("Lumina")
        self.overlay.set_badge("")
        self.overlay.show_welcome(self.welcome)
        self.overlay.poke()
        QTimer.singleShot(250, self.sidebar.tracks.reload)

    def _toggle_mute(self) -> None:
        muted = not bool(self.engine.get("mute", default=False))
        self.engine.set_mute(muted)
        self.overlay.bar.set_volume(int(self.config.get("player.volume", 100)), muted)
        self.overlay.show_osd(i18n.t("静音") if muted else i18n.t("取消静音"), 900)

    def _bump_speed(self, delta: float) -> None:
        current = float(self.engine.get("speed", default=1.0) or 1.0)
        self._set_speed(round(current + delta, 2))

    def _set_speed(self, value: float) -> None:
        value = max(0.1, min(4.0, float(value)))
        self.engine.set_speed(value)
        self.config.set("player.speed", value)
        self.overlay.bar.set_speed(value)
        self.overlay.show_osd(i18n.t("速度 {:.2f}×", value), 900)

    def _cycle_subtitle_track(self) -> None:
        subs = self.engine.tracks_of("sub")
        if not subs:
            self.overlay.show_osd(i18n.t("没有字幕轨（按 T 用 AI 生成）"), 2000)
            return
        ids = [t.get("id") for t in subs]
        current = self.engine.get("sid")
        nxt = ids[(ids.index(current) + 1) % len(ids)] if current in ids else ids[0]
        self.engine.set("sid", nxt)
        if nxt == self.pipeline.ai_pid:
            # 生成轨在后台一直在写盘，切回来时取最新内容
            self.engine.reload_subtitle()
        label = next((t for t in subs if t.get("id") == nxt), {})
        self.overlay.show_osd(i18n.t("字幕：{}", track_row_text(label)), 1800)
        self.sidebar.tracks.reload()

    def _cycle_audio_track(self) -> None:
        audios = self.engine.tracks_of("audio")
        if not audios:
            return
        ids = [t.get("id") for t in audios]
        current = self.engine.get("aid")
        nxt = ids[(ids.index(current) + 1) % len(ids)] if current in ids else ids[0]
        self.engine.select_track("audio", nxt)
        label = next((t for t in audios if t.get("id") == nxt), {})
        self.overlay.show_osd(i18n.t("音轨：{}", track_row_text(label)), 1800)
        self.sidebar.tracks.reload()

    def _cycle_display_mode(self) -> None:
        mode = self.pipeline.cycle_display()
        self.sidebar.tracks.reload()
        self.overlay.show_osd(i18n.t(MODE_LABELS.get(mode, mode)), 1400)

    def _is_cloud_source(self) -> bool:
        source = self.pipeline.media_source
        return bool(source is not None and source.is_remote)

    def _cloud_ai_blocked(self) -> bool:
        source = self.pipeline.media_source
        if source is not None and source.is_remote:
            self.overlay.show_osd(i18n.t("云端原生字幕轨翻译暂不支持；"
                                         "实时翻译/转字幕/一键翻译已可用"), 3600)
            return True
        return False

    def _stop_stream_proxy(self) -> None:
        proxy = self._stream_proxy
        self._stream_proxy = None
        self._proxy_source = None
        if proxy is not None:
            try:
                proxy.stop()
            except Exception as exc:
                cfg.append_log(f"单流代理停止失败：{exc}")

    def _begin_cloud_asr(self, translate: bool) -> bool:
        """流仲裁：停掉 mpv 的 115 流，让 ffmpeg 独占转写；完成/失败后自动回来。"""
        source = self.pipeline.media_source
        if source is None or not source.is_remote:
            return False
        ok, message = self.pipeline.whisper.availability()
        if not ok:
            self.overlay.show_osd("✗ " + message, 4000)
            return False
        if self.pipeline.is_busy():
            self.overlay.show_osd(i18n.t("已有任务在跑，等它结束或点停止"), 2200)
            return True
        audio_index = self.pipeline._audio_ordinal()
        audio_lang = self.pipeline._audio_lang()
        position = float(self.engine.position or 0.0)
        was_playing = not bool(self.engine.get("pause", True))
        self.overlay.show_osd(
            i18n.t("网盘转写：先释放播放流，完成/失败后自动回到原位置…"), 3800)
        try:
            self.engine.set("pause", True)
            self.engine.command("stop")
        except Exception:
            pass
        self.pipeline.generate_ai(
            live=False, translate=translate, released=True,
            audio_index=audio_index, audio_lang=audio_lang)
        # 必须在 generate_ai 的 stop_all 之后再置位，避免它发出的 busy=False
        # 被当成任务结束而提前恢复播放。
        self._arbitrating = True
        self._arb_source = source
        self._arb_position = position
        self._arb_was_playing = was_playing
        if not self.pipeline.is_busy():
            self._resume_cloud_playback()
        return True

    def _on_asr_finished(self) -> None:
        if self._arbitrating:
            self._resume_cloud_playback()

    def _on_pipeline_busy(self, running: bool) -> None:
        if not running and self._arbitrating:
            self._resume_cloud_playback()

    def _resume_cloud_playback(self) -> None:
        if not self._arbitrating:
            return
        source = self._arb_source
        position = self._arb_position
        was_playing = self._arb_was_playing
        self._arbitrating = False
        self._arb_source = None
        if source is None or self._current != source.key:
            return
        try:
            self._pending_source = source
            self.engine.open_media(source.uri, headers=source.headers, start=position)
            self.engine.set("pause", True)
        except Exception as exc:
            self.overlay.show_osd(i18n.t("网盘播放恢复失败：{}", exc), 4000)
            return

        attempts = {"left": 40}

        def finish() -> None:
            if self._current != source.key:
                return
            # 115 的元数据有时比 500 ms 慢；duration 还没出来时 seek 会被丢掉
            if not self.engine.duration and attempts["left"] > 0:
                attempts["left"] -= 1
                QTimer.singleShot(250, finish)
                return
            try:
                self.engine.seek(max(0.0, position), absolute=True)
                self.engine.set("start", 0.0)
                self.engine.set("pause", not was_playing)
            except Exception:
                pass
            self.pipeline.player_released = False
            self.pipeline.audio_index_override = None
            self.pipeline.audio_lang_override = ""
            self.pipeline._render(force=True)
            self.sidebar.tracks.reload()
            self._update_badge()
            self.overlay.show_osd(i18n.t("已回到网盘播放位置"), 1800)

        QTimer.singleShot(300, finish)

    def _begin_cloud_live(self, translate: bool = True) -> bool:
        """云端实时翻译：切到本机单流代理，让 mpv 与 ASR 共用一条 115 上游。"""
        source = self.pipeline.media_source
        if source is None or not source.is_remote:
            return False
        ok, message = self.pipeline.whisper.availability()
        if not ok:
            self.overlay.show_osd("✗ " + message, 4000)
            return False
        if self.pipeline.is_busy():
            self.overlay.show_osd(i18n.t("已有任务在跑，等它结束或点停止"), 2200)
            return True
        if self._cloud_live_starting:
            self.overlay.show_osd(i18n.t("云端实时翻译正在准备单流代理…"), 2200)
            return True
        self.show_sidebar("ai")
        if self._stream_proxy is not None and self._proxy_source is not None \
                and self._proxy_source.key == source.key:
            self.pipeline.generate_ai(live=True, translate=translate, cloud_live=True)
            self.overlay.show_osd(i18n.t("云端实时翻译：继续使用单流代理…"), 2000)
            return True
        position = float(self.engine.position or 0.0)
        was_playing = not bool(self.engine.get("pause", True))
        audio_index = self.pipeline._audio_ordinal()
        audio_lang = self.pipeline._audio_lang()
        try:
            self.engine.set("pause", True)
            self.engine.command("stop")
        except Exception:
            pass
        self._cloud_live_state = (source, position, was_playing,
                                  audio_index, audio_lang, translate)
        self._cloud_live_starting = True
        cfg.append_log("云端实时翻译：准备单流代理")
        self.overlay.show_osd(i18n.t("云端实时翻译：准备单流代理（只缓存内存，不落视频盘）…"), 5000)
        # 先让 OSD 画出来，并等 mpv 旧连接真正断开：紧接着开代理会和旧 115 流并发
        QTimer.singleShot(2500, self._start_cloud_live_proxy)
        return True

    def _start_cloud_live_proxy(self) -> None:
        state = self._cloud_live_state
        self._cloud_live_state = None
        if state is None or self._closing:
            self._cloud_live_starting = False
            return
        source, position, was_playing, audio_index, audio_lang, translate = state
        if self._current != source.key:
            self._cloud_live_starting = False
            return
        self._cloud_live_seq += 1
        self._cloud_live_starting = True
        seq = self._cloud_live_seq
        cfg.append_log("云端实时翻译：后台读取片头/片尾索引")
        threading.Thread(
            target=self._build_cloud_proxy, daemon=True, name="cloud-live-proxy",
            args=((seq, source, position, was_playing, audio_index,
                   audio_lang, translate),)).start()

    def _build_cloud_proxy(self, payload) -> None:
        seq, source, position, was_playing, audio_index, audio_lang, translate = payload
        proxy = None
        try:
            cache_mb = int(self.config.get("cloud.live_cache_mb", 128) or 128)
            proxy = RangeCacheProxy(source, cache_mb=cache_mb)
            url = proxy.start()
        except Exception as exc:
            if proxy is not None:
                try:
                    proxy.stop()
                except Exception:
                    pass
            if not self._closing:
                self.cloud_proxy_failed.emit((seq, str(exc), source, position, was_playing))
            return
        if self._closing:
            try:
                proxy.stop()
            except Exception:
                pass
            return
        self.cloud_proxy_ready.emit((seq, proxy, url, payload))

    def _on_cloud_proxy_failed(self, data) -> None:
        seq, message, source, position, was_playing = data
        if seq != self._cloud_live_seq or self._closing:
            return
        self._cloud_live_starting = False
        cfg.append_log(f"云端实时翻译：代理启动失败：{message}")
        self.overlay.show_osd(i18n.t("云端实时翻译启动失败：{}", message), 6000)
        self._reopen_direct(source, position, was_playing)

    def _on_cloud_proxy_ready(self, data) -> None:
        seq, proxy, url, payload = data
        _, source, position, was_playing, audio_index, audio_lang, translate = payload
        if seq != self._cloud_live_seq or self._closing or self._current != source.key:
            try:
                proxy.stop()
            except Exception:
                pass
            return
        self._cloud_live_starting = False
        proxied = source.via_proxy(url)
        self._stream_proxy = proxy
        self._proxy_source = proxied
        self.pipeline.media_source = proxied
        self.pipeline.current_media = proxied.uri
        self.pipeline.audio_index_override = audio_index
        self.pipeline.audio_lang_override = audio_lang
        self._pending_source = proxied
        self._current_source = proxied
        try:
            self.engine.open_media(proxied.uri, headers={}, start=position,
                                   read_ahead_mb=self._proxy_read_ahead_mb())
            self.engine.set("pause", True)
        except Exception as exc:
            cfg.append_log(f"云端实时翻译：代理播放启动失败：{exc}")
            self._stop_stream_proxy()
            self.pipeline.media_source = source
            self.pipeline.current_media = source.uri
            self._current_source = source
            self.overlay.show_osd(i18n.t("云端实时翻译启动失败：{}", exc), 6000)
            self._reopen_direct(source, position, was_playing)
            return
        self._await_proxy_playback(proxy, proxied, position, was_playing,
                                   audio_index, translate, 40)

    def _proxy_read_ahead_mb(self) -> int:
        """mpv 在单流代理上允许往前预读多少 MiB —— 必须小到和缓存匹配。"""
        cache_mb = int(float(self.config.get("cloud.live_cache_mb", 128) or 128))
        return max(4, cache_mb // 8)

    def _apply_proxy_lead_budget(self, proxy) -> None:
        """把「缓存装得下几秒」告诉管线，别让云端预读按秒数冲出去。"""
        try:
            seconds = proxy.max_lead_seconds(self.engine.duration or 0.0)
        except Exception:
            seconds = 0.0
        self.pipeline.cloud_lead_cap = round(seconds, 1) if seconds > 0 else 0.0
        cfg.append_log(
            f"云端预读上限：按码率算最多超前 {self.pipeline.cloud_lead_cap:.0f} 秒"
            f"（缓存 {int(float(self.config.get('cloud.live_cache_mb', 128) or 128))} MB）"
            if self.pipeline.cloud_lead_cap else "云端预读上限：算不出码率，沿用设置值")

    def _await_proxy_playback(self, proxy, source: MediaSource, position: float,
                              was_playing: bool, audio_index: int, translate: bool,
                              left: int) -> None:
        if self._closing or self._stream_proxy is not proxy or self._current != source.key:
            return
        if not self.engine.duration and left > 0:
            QTimer.singleShot(
                250, lambda: self._await_proxy_playback(
                    proxy, source, position, was_playing, audio_index, translate, left - 1))
            return
        if not self.engine.duration:
            cfg.append_log("云端实时翻译：代理已启动，但播放器没读到片长，回退直连")
            self.overlay.show_osd(i18n.t("单流代理没读到片长，已回退直接播放"), 5000)
            original = getattr(proxy, "source", None)
            self._stop_stream_proxy()
            if original is not None:
                self.pipeline.media_source = original
                self.pipeline.current_media = original.uri
                self._current_source = original
                self._reopen_direct(original, position, was_playing)
            return
        try:
            internal = [t for t in self.engine.tracks_of("audio") if not t.get("external")]
            if 0 <= audio_index < len(internal):
                self.engine.select_track("audio", internal[audio_index].get("id"))
            self.engine.seek(max(0.0, position), absolute=True)
            self.engine.set("start", 0.0)
            self.engine.set("pause", not was_playing)
        except Exception:
            pass
        self.pipeline.audio_index_override = None
        self._apply_proxy_lead_budget(proxy)
        self.pipeline.audio_lang_override = ""
        cfg.append_log("云端实时翻译：单流代理就绪，开始实时转写")
        self.overlay.show_osd(i18n.t("云端实时翻译：单流代理就绪，开始实时转写…"), 2600)
        self.pipeline.generate_ai(live=True, translate=translate, cloud_live=True)

    def _reopen_direct(self, source: MediaSource, position: float,
                       was_playing: bool) -> None:
        if self._current != source.key:
            return
        self._pending_source = source
        try:
            self.engine.open_media(source.uri, headers=source.headers, start=position)
            self.engine.set("pause", True)
        except Exception:
            return
        attempts = {"left": 40}

        def finish() -> None:
            if self._current != source.key:
                return
            if not self.engine.duration and attempts["left"] > 0:
                attempts["left"] -= 1
                QTimer.singleShot(250, finish)
                return
            try:
                self.engine.seek(max(0.0, position), absolute=True)
                self.engine.set("start", 0.0)
                self.engine.set("pause", not was_playing)
            except Exception:
                pass
            self.overlay.show_osd(i18n.t("已恢复直接播放"), 1800)

        QTimer.singleShot(300, finish)

    def _request_ai(self, live: bool) -> None:
        if not self._current:
            self.overlay.show_osd(i18n.t("先打开一个视频"), 2000)
            return
        if self._cloud_live_starting:
            self.overlay.show_osd(i18n.t("云端实时翻译正在准备单流代理，等它就绪"), 2400)
            return
        if self.pipeline.is_busy():
            self.overlay.show_osd("已有任务在跑，等它结束或点停止", 2200)
            return
        self.show_sidebar("ai")
        if live:
            problem = translate.model_problem(self.config, live=True)
            if problem:
                # 模型没就绪就别开任务：否则转写在跑、翻译一句不出，看不出原因
                cfg.append_log(f"实时翻译没启动：{problem}")
                self.overlay.show_osd(problem, 7000)
                self.sidebar.ai.note(i18n.t("实时翻译没启动：{}", problem))
                return
        if self._is_cloud_source():
            if live:
                self._begin_cloud_live(translate=True)
                return
            self._begin_cloud_asr(translate=False)
            return
        self.pipeline.generate_ai(live=live, translate=live)
        self.overlay.show_osd(i18n.t("实时翻译：转写 + 翻译…") if live
                              else i18n.t("转字幕：开始（不翻译）"), 1800)

    def _request_one_click(self) -> None:
        if not self._current:
            self.overlay.show_osd(i18n.t("先打开一个视频"), 2000)
            return
        if self._cloud_live_starting:
            self.overlay.show_osd(i18n.t("云端实时翻译正在准备，等它就绪"), 2400)
            return
        if self.pipeline.is_busy():
            self.overlay.show_osd("已有任务在跑，等它结束或点停止", 2200)
            return
        self.show_sidebar("ai")
        problem = translate.model_problem(self.config, live=False)
        if problem:
            cfg.append_log(f"一键翻译没启动：{problem}")
            self.overlay.show_osd(problem, 7000)
            self.sidebar.ai.note(i18n.t("一键翻译没启动：{}", problem))
            return
        source = self.pipeline.source
        hint = self.pipeline.language_hint()
        language_changed = (bool(self.pipeline.cues) and source is not None
                            and source.origin == "ai"
                            and not self.pipeline.transcript_matches_language(hint))
        reusable = (bool(self.pipeline.cues) and source is not None
                    and source.origin == "ai"
                    and self.pipeline.transcript_complete()
                    and not language_changed)
        if reusable:
            cfg.append_log(f"一键翻译：复用完整转写（{len(self.pipeline.cues)} 句）")
            self.pipeline.translate_generated()      # 已经转好，只补翻译
            self.overlay.show_osd(i18n.t("一键翻译：翻译已转写的字幕…"), 1800)
            return
        if language_changed:
            wanted = cfg.lang_display(hint)
            current = (cfg.lang_display(self.pipeline.transcript_language())
                       or self.pipeline.transcript_language() or "未知")
            cfg.append_log(
                f"一键翻译：识别语言已改为{wanted}（现有字幕是{current}），重新转写")
        if self._is_cloud_source():
            if language_changed:
                self.overlay.show_osd(
                    i18n.t("识别语言改为{}：重新转写 + 翻译…", wanted), 3600)
            self._begin_cloud_asr(translate=True)
            return
        stale = bool(self.pipeline.cues and source is not None and source.origin == "ai")
        cfg.append_log("一键翻译：上次字幕不完整或来自旧版本，重新转写"
                       if stale and not language_changed else "一键翻译：转写 + 翻译")
        self.pipeline.generate_ai(live=False, translate=True)
        if language_changed:
            text = i18n.t("识别语言改为{}：重新转写 + 翻译…", wanted)
        elif stale:
            text = i18n.t("一键翻译：上次字幕不完整，重新转写 + 翻译…")
        else:
            text = i18n.t("一键翻译：转写 + 翻译…")
        self.overlay.show_osd(text, 2600 if (stale or language_changed) else 1800)

    def _translate_track(self, track_id) -> None:
        if self._is_cloud_source() and track_id != self.pipeline.ai_pid:
            self._cloud_ai_blocked()
            return
        if self.pipeline.is_busy():
            self.overlay.show_osd(i18n.t("已有任务在跑，等它结束再翻译"), 2200)
            return
        if self.pipeline.ai_pid is not None and track_id == self.pipeline.ai_pid:
            hint = self.pipeline.language_hint()
            if not self.pipeline.transcript_matches_language(hint):
                wanted = cfg.lang_display(hint)
                current = (cfg.lang_display(self.pipeline.transcript_language())
                           or self.pipeline.transcript_language() or "未知")
                self.overlay.show_osd(
                    f"现有 AI 字幕是{current}，识别语言已改为{wanted}；"
                    f"请点「一键翻译」重新转写", 4600)
                return
            self.pipeline.translate_generated()
            return
        track = next((t for t in self.engine.tracks_of("sub") if t.get("id") == track_id), None)
        if track is None:
            return
        self.pipeline.translate_track(track)

    def _screenshot(self) -> None:
        folder = cfg.WORK_DIR / "screenshots"
        folder.mkdir(parents=True, exist_ok=True)
        if self.backend == "libmpv":
            stem = f"lumina-{time.strftime('%H-%M-%S')}"
            path = folder / f"{stem}.png"
            n = 1
            while path.exists():                       # 连按两次别把刚存的图冲掉
                path = folder / f"{stem}-{n}.png"
                n += 1
            if self.video_host.grab_frame().save(str(path)):
                self.overlay.show_osd(i18n.t("截图已存：{}", path.name), 2600)
            else:
                self.overlay.show_osd(i18n.t("截图失败：画面还没出来"), 2600)
            return
        self.engine.screenshot()
        self.overlay.show_osd(i18n.t("截图已存到 {}", folder), 2600)

    def _debug_state(self) -> None:
        lines = [
            f"backend={self.backend} gl帧={getattr(self.video_host, 'frames', -1)}",
            f"hwdec want={self.config.get('player.hwdec')} actual={self.engine.hwdec_actual()}",
            f"tracks: video={len(self.engine.tracks_of('video'))} audio={len(self.engine.tracks_of('audio'))} sub={len(self.engine.tracks_of('sub'))}",
            f"sid={self.engine.get('sid')} aid={self.engine.get('aid')} ai_pid={self.pipeline.ai_pid}",
            f"cues={len(self.pipeline.cues)} translated={sum(1 for t in self.pipeline.translated if t)}",
            f"cache hits={self.pipeline.cache.hits} misses={self.pipeline.cache.misses}",
        ]
        self.overlay.show_osd(" | ".join(lines), 9000)

    # ------------------------------------------------------------ 侧栏/全屏

    def show_sidebar(self, page: str) -> None:
        index = PAGE_INDEX[page]
        self.sidebar.show_page(index, CLOUD_SUBPAGE.get(page))
        self.sidebar.setVisible(True)
        self.sidebar.tracks.reload()
        QTimer.singleShot(40, self._sync_overlay)

    def hide_sidebar(self) -> None:
        self.sidebar.setVisible(False)
        QTimer.singleShot(40, self._sync_overlay)

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen() or self.windowState() & Qt.WindowFullScreen:
            self._exit_fullscreen()
        else:
            self._normal_geometry = self.geometry()
            self.sidebar.setVisible(False)
            self.showFullScreen()
        QTimer.singleShot(280, self._sync_overlay)

    def _exit_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
            geometry = getattr(self, "_normal_geometry", None)
            if geometry:
                self.setGeometry(geometry)
        QTimer.singleShot(280, self._sync_overlay)

    def isPaused(self) -> bool:  # noqa: N802
        return self._paused or not self._current

    def _sync_overlay(self) -> None:
        self.overlay.sync_geometry(self.video_area)
        self.overlay.poke()
        self._pin_player_window()

    def _pin_player_window(self, force: bool = False) -> None:
        """内嵌不可用时，把 mpv 的播放窗贴成我们视频区的大小与位置。"""
        if self.backend != "ipc":
            return
        if self.config.get("player.embed_mpv", False) or not self.config.get("player.pin_window", True):
            return
        if not self.engine.is_alive():
            return
        origin = self.video_area.mapToGlobal(QPoint(0, 0))
        # 两处口径差：① mpv 的 geometry 按像素解释，Qt 坐标是点（Retina 差 2x）；
        # ② mpv 的偏移从「可视区」算起，Qt 全局坐标从整屏原点算，差一个菜单栏厚度。
        screen = self.windowHandle().screen() or QGuiApplication.primaryScreen()
        offset_x = screen.availableGeometry().left() - screen.geometry().left()
        offset_y = screen.availableGeometry().top() - screen.geometry().top()
        ratio = screen.devicePixelRatio() or 1.0
        self.engine.pin_to_rect(int((origin.x() - offset_x) * ratio),
                                int((origin.y() - offset_y) * ratio),
                                int(self.video_host.width() * ratio),
                                int(self.video_host.height() * ratio),
                                force=force)

    # ------------------------------------------------------------ 几何/生命周期

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.overlay.show()
        self._sync_overlay()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_overlay()

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        self._sync_overlay()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape and self.sidebar.isVisible():
            self.hide_sidebar()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._closing = True
        self._flush_volume()
        self._cloud_live_state = None
        self._cloud_live_seq += 1
        self._cloud_live_starting = False
        self.pipeline.stop_all()
        self._stop_stream_proxy()
        self.shutdown_cloud()
        # 渲染上下文持有 GL 资源，必须在 mpv 句柄释放前先拆掉，顺序反了会 use-after-free
        dispose = getattr(self.video_host, "dispose", None)
        if callable(dispose):
            dispose()
        self.engine.quit()
        super().closeEvent(event)


def track_row_text(track: dict) -> str:
    return track_row(track) if track else "关闭"
