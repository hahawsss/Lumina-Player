"""应用入口：QApplication、原生菜单、Finder 打开文件事件、主题装配。"""
from __future__ import annotations

import os
import signal
import sys
import threading
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox

from . import asr, config as cfg, translate
from .ui.main_window import MainWindow
from .ui.theme import build_qss

SHORTCUT_HELP = """空格 / K　播放·暂停        ← / →　-10s / +30s
Shift+← / →　±60s        Alt+← / →　上/下一个
↑ / ↓　音量        M　静音
[ / ]　速度 ±0.25        Backspace　恢复 1×
, / .　逐帧        F　全屏        Esc　退出全屏 / 关侧栏
S　切字幕轨        A　切音轨        Z　原文→双语→译文
T　AI 字幕面板        G　轨道面板        I　设置面板        L　播放列表
D　网盘（115 浏览 / OpenList 管理）        R　存储与清理面板
Ctrl+O　打开        Ctrl+S　加字幕文件        ⌘,　设置        Ctrl+H　收侧栏"""


class LuminaApp(QApplication):
    """Finder 双击 / 右键「打开方式」进来的文件走 FileOpen 事件，不是 argv。"""

    file_open = Signal(str)

    def __init__(self, argv):
        super().__init__(argv)
        self.setApplicationName("Lumina Player")
        self.setOrganizationName("Lumina")
        self.setApplicationDisplayName("Lumina")

    def event(self, event):  # noqa: D401
        if event.type() == QEvent.FileOpen:
            self.file_open.emit(event.file())
            return True
        return super().event(event)


def paths_from_argv(argv) -> list[str]:
    out = []
    for item in argv[1:]:
        if item.startswith("-"):
            continue
        path = Path(item).expanduser()
        if path.is_dir():
            out += [str(p) for p in sorted(path.rglob("*")) if p.suffix.lower() in cfg.MEDIA_EXTS]
        elif path.exists():
            out.append(str(path))
    return out


def build_menu(app: QApplication, window: MainWindow) -> None:
    menubar = window.menuBar()
    menubar.setNativeMenuBar(True)

    file_menu = menubar.addMenu("文件")
    open_action = QAction("打开视频…", app)
    open_action.setShortcut(QKeySequence("Ctrl+O"))
    open_action.triggered.connect(window.open_file_dialog)
    file_menu.addAction(open_action)
    folder_action = QAction("打开文件夹…", app)
    folder_action.triggered.connect(window.open_folder_dialog)
    file_menu.addAction(folder_action)
    sub_action = QAction("添加字幕文件…", app)
    sub_action.setShortcut(QKeySequence("Ctrl+S"))
    sub_action.triggered.connect(window._add_subtitle_file)
    file_menu.addAction(sub_action)
    file_menu.addSeparator()
    recent = file_menu.addMenu("最近打开")
    window.recent_menu = recent
    for path in window.config.recent[:12]:
        action = QAction(Path(path).name, app)
        action.triggered.connect(lambda _=False, p=path: window.open_path(p))
        recent.addAction(action)
    recent.aboutToShow.connect(lambda: window.refresh_recent_menu(recent))

    playback = menubar.addMenu("播放")
    pause_action = QAction("播放/暂停", app)
    pause_action.setShortcut(QKeySequence("Space"))
    pause_action.triggered.connect(window.toggle_play)
    playback.addAction(pause_action)
    for label, delta in (("后退 10 秒", -10), ("前进 30 秒", 30), ("后退 60 秒", -60), ("前进 60 秒", 60)):
        action = QAction(label, app)
        action.triggered.connect(lambda _=False, d=delta: window.engine.jump(d))
        playback.addAction(action)
    playback.addSeparator()
    speed_menu = playback.addMenu("速度")
    group = QActionGroup(app)
    for value in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
        action = QAction(f"{value:g}×", app)
        action.setCheckable(True)
        action.setChecked(value == 1.0)
        action.triggered.connect(lambda _=False, v=value: window._set_speed(v))
        group.addAction(action)
        speed_menu.addAction(action)

    view = menubar.addMenu("显示")
    for label, page in (("音轨与字幕", "tracks"), ("AI 字幕", "ai"),
                        ("设置", "settings"), ("播放列表", "playlist"),
                        ("存储与清理", "storage"), ("网盘（115 / OpenList）", "115"),
                        ("OpenList 管理", "cloud_manage")):
        action = QAction(label, app)
        action.triggered.connect(lambda _=False, p=page: window.show_sidebar(p))
        view.addAction(action)
    fullscreen_action = QAction("全屏", app)
    fullscreen_action.setShortcut(QKeySequence("F"))
    fullscreen_action.triggered.connect(window.toggle_fullscreen)
    view.addAction(fullscreen_action)

    help_menu = menubar.addMenu("帮助")
    about_keys = QAction("快捷键", app)
    about_keys.setMenuRole(QAction.NoRole)
    about_keys.triggered.connect(lambda: QMessageBox.information(window, "快捷键", SHORTCUT_HELP))
    help_menu.addAction(about_keys)
    about = QAction("关于 Lumina", app)
    about.setMenuRole(QAction.AboutRole)
    about.triggered.connect(window.show_about)
    help_menu.addAction(about)
    prefs = QAction("设置…", app)
    prefs.setMenuRole(QAction.PreferencesRole)
    prefs.setShortcut(QKeySequence("Ctrl+,"))
    prefs.triggered.connect(lambda: window.show_sidebar("settings"))
    help_menu.addAction(prefs)
    diagnose = QAction("环境自检…", app)
    diagnose.setMenuRole(QAction.NoRole)
    diagnose.triggered.connect(window.show_doctor)
    help_menu.addAction(diagnose)


def install_signal_cleanup(window: MainWindow) -> None:
    """SIGTERM/SIGINT 的默认处置不会跑 atexit，mpv 会变成孤儿进程，所以自己收。"""

    def handler(signum, _frame) -> None:
        cfg.append_log(f"收到信号 {signum}，先收掉后台任务与 mpv 子进程")
        try:
            window.pipeline.stop_all()
        finally:
            window.shutdown_cloud()
            window.engine.quit()
        QApplication.quit()

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


def _take_shots(window: MainWindow, out: Path) -> None:
    """无录屏权限下的 UI 自检拍照：libmpv 后端时画面就画在我们自己的控件里，
    window.grab() 连视频带字幕一起拍得下来（ipc 后端那块是原生子视图，会是黑框）。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    saved = []
    for page in ("tracks", "settings", "ai", "playlist"):
        window.show_sidebar(page)
        window.overlay.bar.setVisible(True)
        QApplication.processEvents()
        time.sleep(0.3)
        QApplication.processEvents()
        path = out.with_name(f"{out.stem}-{page}.png")
        window.grab().save(str(path))
        saved.append(str(path))
    window.hide_sidebar()
    QApplication.processEvents()
    time.sleep(0.2)
    window.overlay.bar.setVisible(True)
    overlay_path = out.with_name(f"{out.stem}-overlay.png")
    window.overlay.grab().save(str(overlay_path))
    saved.append(str(overlay_path))
    print("SELFSHOT " + " ".join(saved), flush=True)
    QApplication.quit()


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    cfg.ensure_dirs()
    cfg.append_log(f"===== 启动，参数={argv[1:]}")
    moved = cfg.migrate_legacy_subtitles()
    if moved:
        cfg.append_log(f"旧目录的生成字幕已搬进 subtitles/：{moved} 份")
    moved = cfg.migrate_legacy_models()
    if moved:
        cfg.append_log(f"旧版的转写模型已搬进 models/whisper/：{moved} 个")
    stale_live = cfg.clean_live_subtitles()
    if stale_live:
        cfg.append_log(f"清掉上次遗留的实时字幕临时文件：{stale_live} 个")

    def hook(etype, value, tb) -> None:
        cfg.append_log("".join(traceback.format_exception(etype, value, tb)).strip())
        sys.__excepthook__(etype, value, tb)

    sys.excepthook = hook
    app = LuminaApp(argv)
    config = cfg.Config()
    translate.migrate_model_split(config)
    asr.warm_async(config)      # whisper --help 要半秒多，先在后台热起来
    app.setStyleSheet(build_qss(config.get("ui.accent", "#4fa8ff")))

    window = MainWindow(config, paths_from_argv(argv))
    # 每次启动都记下实际走的后端与环境快照：mpv_create 这类「只返回空、不报错」的失败，
    # 事后唯一的线索就是当时这台机器处于什么状态
    import resource
    soft, hard = resource.getrlimit(resource.RLIMIT_NPROC)
    cfg.append_log(
        f"后端={window.backend} 引擎={type(window.engine).__name__} · "
        f"HOME={'有' if os.environ.get('HOME') else '无'} "
        f"TMPDIR={os.environ.get('TMPDIR') or '无'} · "
        f"NPROC={soft}/{hard} 本进程线程={threading.active_count()}")
    window.resize(1180, 700)
    build_menu(app, window)
    app.file_open.connect(window.open_path)
    # ⌘Q 走 aboutToQuit、点红叉走 closeEvent、kill 走信号钩子，三条路都不许留网关孤儿
    app.aboutToQuit.connect(window.shutdown_cloud)
    window.show()
    install_signal_cleanup(window)
    shot = os.environ.get("LUMINA_SELFSHOT")
    if shot:
        delay = int(os.environ.get("LUMINA_SELFSHOT_DELAY", "4500"))
        QTimer.singleShot(delay, lambda: _take_shots(window, Path(shot)))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
