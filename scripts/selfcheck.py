#!/usr/bin/env python
"""Lumina 数值自检：不靠肉眼看界面，先把结构事实钉住。

跑法：
    uv run python scripts/selfcheck.py            # 结构 + 引擎 + 管线（约 20 s）
    uv run python scripts/selfcheck.py --asr      # 再加 whisper 实时窗口转写
    uv run python scripts/selfcheck.py --fixture-only

测试素材由 say + ffmpeg 现场生成：2 条音轨（英/中）+ 2 条字幕轨（eng/chi）。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication  # noqa: E402

from lumina import asr, config as cfg, langtags, storage, subtitles, translate  # noqa: E402
from lumina.ui import panels  # noqa: E402
from lumina.ui import theme  # noqa: E402
from lumina.engine import MpvController  # noqa: E402
from lumina.pipeline import PipelineError, SubtitlePipeline  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
FIXTURE_DIR = cfg.WORK_DIR / "fixture"


def check(name: str, condition, detail: str = "") -> bool:
    ok = bool(condition)
    RESULTS.append((name, ok, detail))
    mark = "OK  " if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail and not ok else ""))
    return ok


def section(title: str) -> None:
    print(f"\n=== {title} ===")


# ---------------------------------------------------------------- 素材

def pick_voice(patterns: list[str]) -> str | None:
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    except OSError:
        return None
    for line in out.splitlines():
        for pattern in patterns:
            if pattern.lower() in line.lower():
                return line.split()[0] if not line.startswith("20") else line.split()[0]
    return None


def write_srt(path: Path, lines: list[str]) -> None:
    blocks = []
    for index, text in enumerate(lines):
        start = index * 2.0
        blocks.append(f"{index + 1}\n{subtitles.format_time_srt(start)} --> "
                      f"{subtitles.format_time_srt(start + 1.8)}\n{text}\n")
    path.write_text("\n".join(blocks), encoding="utf-8")


def build_fixture(force: bool = False) -> Path:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    media = FIXTURE_DIR / "multi.mkv"
    if media.exists() and not force:
        return media
    en_voice = pick_voice(["Alex", "Flo (U.S.)", "Flo (英国)"]) or "Alex"
    zh_voice = pick_voice(["Tingting", "Meijia", "Sinji", "Yu-shu"])
    en_lines = [
        "The harbor lights are still on.",
        "Nobody told him the ferry stopped.",
        "He waited until the fog came in.",
        "Morning came without a sound.",
    ]
    zh_lines = [
        "港口的灯还亮着。",
        "没人告诉他渡轮已经停了。",
        "他一直等到雾涌进来。",
        "清晨无声无息地来了。",
    ]
    en_aiff, zh_aiff = FIXTURE_DIR / "en.aiff", FIXTURE_DIR / "zh.aiff"
    for voice, path, lines in ((en_voice, en_aiff, en_lines), (zh_voice, zh_aiff, zh_lines)):
        if voice is None and path is zh_aiff:
            # 没有中文语音就先静音占位，保证轨道结构仍然可测
            with wave.open(str(FIXTURE_DIR / "zh.wav"), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(22050)
                handle.writeframes(b"\x00\x00" * 22050 * 8)
            path = FIXTURE_DIR / "zh.wav"
        else:
            subprocess.run(["say", "-v", voice, "-o", str(path),
                            ". ".join(lines)], check=True, capture_output=True)
    write_srt(FIXTURE_DIR / "en.srt", en_lines)
    write_srt(FIXTURE_DIR / "zh.srt", zh_lines)

    base = FIXTURE_DIR / "base.mkv"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=8",
         "-i", str(en_aiff), "-i", str(zh_aiff),
         "-map", "0:v", "-map", "1:a", "-map", "2:a",
         "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "96k",
         "-metadata:s:a:0", "language=eng", "-metadata:s:a:0", "title=English",
         "-metadata:s:a:1", "language=zho", "-metadata:s:a:1", "title=中文",
         str(base)], check=True, capture_output=True)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(base), "-i", str(FIXTURE_DIR / "en.srt"), "-i", str(FIXTURE_DIR / "zh.srt"),
         "-map", "0", "-map", "1", "-map", "2", "-c", "copy", "-c:s", "srt",
         "-metadata:s:s:0", "language=eng", "-metadata:s:s:1", "language=chi",
         str(media)], check=True, capture_output=True)
    return media


# ---------------------------------------------------------------- 检查项

def check_icons() -> None:
    section("图标渲染")
    from PySide6.QtWidgets import QApplication
    names = sorted(theme._SHAPES.keys())
    empty = []
    for name in names:
        pix = theme.icon(name, "#ffffff", 22).pixmap(22, 22)
        image = pix.toImage()
        painted = sum(1 for y in range(image.height()) for x in range(image.width())
                      if image.pixelColor(x, y).alpha() > 8)
        if painted < 30:
            empty.append(f"{name}={painted}")
    check(f"{len(names)} 个图标全部画得出像素", not empty, empty)


def check_ui() -> None:
    section("UI 冒烟（离屏构造，不起 mpv）")
    from PySide6.QtWidgets import (QApplication, QLabel, QMessageBox, QPushButton,
                                   QToolButton, QWidget)
    from lumina.ui import panels
    from lumina.ui.main_window import MainWindow

    config = cfg.Config(cfg.SUPPORT_DIR / "selfcheck_ui.toml")
    config.set("player.backend", "ipc")      # 真内嵌由 verify_embed 判，这里验退路那条线
    config.set("cloud.start_mode", "manual")  # 测试绝不去启动/停止/换血用户那份共享网关
    config.set("player.embed_mpv", False)
    QApplication.instance().setStyleSheet(theme.build_qss(config.get("ui.accent", "#4fa8ff")))
    crashed = None
    window = None
    try:
        window = MainWindow(config)
        window._start_engine = lambda: None      # UI 冒烟不拉 mpv
        window.resize(1100, 700)
        window.show()
        for page in ("tracks", "ai", "settings", "playlist", "storage", "115",
                     "cloud_manage"):
            window.show_sidebar(page)
            QApplication.processEvents()
        window.overlay.sync_geometry(window.video_host)
        QApplication.processEvents()
    except Exception as exc:
        crashed = f"{type(exc).__name__}: {exc}"
    check("主窗口 + 六个面板可构造可显示", crashed is None, crashed)
    if window is None:
        return
    buttons = [b for b in window.overlay.bar.findChildren(QToolButton) if not b.text()]
    blank = []
    for button in buttons:
        image = button.icon().pixmap(22, 22).toImage()
        painted = sum(1 for y in range(image.height()) for x in range(image.width())
                      if image.pixelColor(x, y).alpha() > 8)
        if painted < 20:
            blank.append(button.toolTip() or "?")
    check(f"控制条 {len(buttons)} 个纯图标按钮都画得出来", not blank, blank)
    check("侧栏宽度固定不挤压画面", window.sidebar.width() == 348, window.sidebar.width())
    tabs = window.sidebar.buttons
    tight = [f"{button.text()}:{button.width()}<提示{button.sizeHint().width()}"
             f"/右缘{button.x() + button.width()}>{window.sidebar.width()}"
             for button in tabs
             if button.width() < button.sizeHint().width()
             or button.x() + button.width() > window.sidebar.width()]
    check("标签排成 3 列 2 行且都没被挤成省略号",
          [t.text() for t in tabs] == list(panels.Sidebar.PAGES)
          and tabs[0].y() != tabs[3].y() and not tight,
          (tight, [(t.text(), t.width(), t.sizeHint().width(), t.x(), t.y()) for t in tabs]))
    window.show_sidebar("storage")
    end = time.time() + 0.4
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.02)
    storage_panel = window.sidebar.storage
    listed = set(storage_panel._sizes)
    check("存储栏把产出/缓存/日志路径都列成可打开的行",
          {str(cfg.WORK_DIR), str(cfg.LIVE_DIR), str(cfg.APP_LOG_PATH),
           str(cfg.LANGUAGE_HINTS_PATH), str(cfg.SUPPORT_DIR)} <= listed,
          sorted({str(cfg.WORK_DIR), str(cfg.APP_LOG_PATH)} - listed))
    ledger = {str(item.path) for _s, items in storage.sections(window.config)
              for item in items}
    no_open = [path for path in ledger
               if path in storage_panel._sizes
               and not any(button.text() == "打开"
                           for button in storage_panel._sizes[path].parent()
                           .findChildren(QPushButton))]
    check("台账行一律给出「打开」，能直接进 Finder", not no_open, no_open[:3])
    no_delete = [model.name for model in storage.installed_models(window.config)
                 if str(model.path) in storage_panel._sizes
                 and not any(button.text() == "删除"
                             for button in storage_panel._sizes[str(model.path)]
                             .parent().findChildren(QPushButton))]
    check("已下载模型逐行给出「删除」", not no_delete, no_delete[:3])
    window.show_sidebar("tracks")
    QApplication.processEvents()
    # 量真实几何，不量 sizeHint：一个控件的最小宽度会顶着滚动区把整块 body 撑宽，
    # sizeHint 看不出来（语言偏好的「下拉 + 4 按钮」一行就是这么把设置栏顶到 358 的）
    over = []
    for page in ("tracks", "ai", "settings", "playlist", "storage", "115",
                 "cloud_manage"):
        window.show_sidebar(page)
        # 滚动区的 resize 是延后一轮的，只 processEvents 两次量到的还是旧宽度
        end = time.time() + 0.25
        while time.time() < end:
            QApplication.processEvents()
            time.sleep(0.02)
        panel = window.sidebar.stack.currentWidget()
        for widget in panel.findChildren(QWidget):
            if not widget.isVisible():
                continue
            right = widget.mapTo(panel, widget.rect().topLeft()).x() + widget.width()
            if right > panel.width() + 1:
                over.append(f"{page}:{type(widget).__name__}"
                            f"「{str(getattr(widget, 'text', lambda: '')())[:12]}」"
                            f"{right}>{panel.width()}")
    window.show_sidebar("tracks")
    QApplication.processEvents()
    check("所有面板的可见控件都没越过面板右边缘", not over, over[:6])
    # QFormLayout 自己造的标签没有 objectName，不给兜底色就是深色底上的纯黑
    dark = [label.text()[:12] for label in window.sidebar.findChildren(QLabel)
            if label.palette().color(label.foregroundRole()).lightness() < 90]
    check("侧栏没有近黑色的标签文字", not dark, dark)
    labels = [label.text() for label in window.sidebar.findChildren(QLabel)]
    check("面板文案非空", any("音轨" in text for text in labels), len(labels))
    # 确认框：全局 QSS 把 QPushButton 画成透明底 + 近白字，对话框底色是浅色时就等于没按钮
    from PySide6.QtCore import QPoint
    box = panels._confirm_box(window, "删除模型", "要从磁盘删除，确定删除？", "确定删除")
    box.adjustSize()
    box.show()
    QApplication.processEvents()
    yes = box.button(QMessageBox.Yes)
    origin = yes.mapTo(box, QPoint(0, 0))
    shot = box.grab().toImage()
    shades = []
    for y in range(origin.y() + 3, min(origin.y() + yes.height(), shot.height()) - 3, 2):
        for x in range(origin.x() + 3, min(origin.x() + yes.width(), shot.width()) - 3, 2):
            color = shot.pixelColor(x, y)
            shades.append(0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue())
    spread = (max(shades) - min(shades)) if shades else 0
    check("确认框的「确定/取消」看得见（按钮区域明暗差≥40）",
          spread >= 40, f"spread={spread} 采样={len(shades)}")
    check("确认框是中文按钮，且默认键落在「取消」上",
          yes.text() == "确定删除" and box.button(QMessageBox.No).text() == "取消"
          and box.defaultButton() is box.button(QMessageBox.No)
          and yes.property("role") == "primary",
          (yes.text(), box.button(QMessageBox.No).text(),
           box.defaultButton(), yes.property("role")))
    box.close()
    box.deleteLater()
    window.close()
    QApplication.processEvents()
    (cfg.SUPPORT_DIR / "selfcheck_ui.toml").unlink(missing_ok=True)


def check_doctor() -> None:
    section("环境体检与自愈")
    from lumina import doctor
    config = cfg.Config(cfg.SUPPORT_DIR / "selfcheck_doctor.toml")
    items = doctor.check(config)
    keys = {item.key for item in items}
    check("体检覆盖 7 个环节", {"mpv", "ffmpeg", "ffprobe", "whisper", "model", "key",
                                "llama"} <= keys, keys)
    mpv_item = next(item for item in items if item.key == "mpv")
    check("libmpv 后端认的是 libmpv 动态库",
          mpv_item.ok and "libmpv" in mpv_item.detail, mpv_item.detail)
    embed_item = next(item for item in items if item.key == "embed")
    check("libmpv 后端的体检说画面画进本窗口",
          "GL 内嵌" in embed_item.detail, embed_item.detail)
    config.set("player.backend", "ipc")
    ipc_items = doctor.check(config)
    ipc_mpv = next(item for item in ipc_items if item.key == "mpv")
    check("ipc 后端认的是 mpv 可执行文件",
          ipc_mpv.ok and ipc_mpv.detail.endswith("mpv"), ipc_mpv.detail)
    installer = doctor.Installer()
    ready = [doctor.Item("mpv", "mpv", True, "/x"), doctor.Item("ff", "ffmpeg", True, "/y", formula="ffmpeg")]
    check("全部就绪时安装清单为空", installer.formulas(ready) == [], installer.formulas(ready))
    broken = [doctor.Item("mpv", "mpv", False, "", formula="mpv"),
              doctor.Item("key", "密钥", False, "", formula="")]
    check("缺失且可装的组件进清单", installer.formulas(broken) == ["mpv"], installer.formulas(broken))
    check("没公式的组件不会被误装", all("key" != f for f in installer.formulas(broken)))
    if translate.find_llama_binary(config):
        llama_item = next(item for item in items if item.key == "llama")
        check("llama.cpp 已装、只是缺模型时不再提示 brew 安装",
              not llama_item.installable, llama_item.detail)
    try:
        asr.find_binary(str(config.get("asr.binary", "whisper-cli")))
        whisper_installed = True
    except asr.AsrError:
        whisper_installed = False
    if whisper_installed:
        whisper_item = next(item for item in items if item.key == "whisper")
        check("whisper 已装、只是缺模型时不再提示 brew 安装",
              not whisper_item.installable, whisper_item.detail)
    check("摘要在有缺失时给出说明", "待补" in doctor.summarize(
        [doctor.Item("model", "转写模型", False, "无", required=False)]), doctor.summarize(
        [doctor.Item("model", "转写模型", False, "无", required=False)]))
    check("必需组件缺失时摘要报警", "缺少必需组件" in doctor.summarize(
        [doctor.Item("mpv", "播放引擎 mpv", False, "没有")]))
    (cfg.SUPPORT_DIR / "selfcheck_doctor.toml").unlink(missing_ok=True)


def check_boot() -> None:
    section("启动不阻塞（就是鼠标转圈那类问题）")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from lumina.ui.main_window import MainWindow

    config = cfg.Config(cfg.SUPPORT_DIR / "selfcheck_boot.toml")
    config.set("player.backend", "ipc")
    config.set("cloud.start_mode", "manual")   # 同上：不碰用户的网关
    config.set("player.embed_mpv", False)
    started = time.time()
    window = MainWindow(config)
    ctor = time.time() - started
    check("主窗口构造不跑子进程 (<0.4s)", ctor < 0.4, f"{ctor * 1000:.0f} ms")
    # 真起 mpv 验证异步拉起不堵主线程，但用无窗参数，免得自检时桌面上乱冒窗口
    real_launch = window.engine.launch
    window.engine.launch = lambda wid=None, extra=(): real_launch(
        wid=None, extra=("--vo=gpu", "--ao=null", "--force-window=no"))

    stamps: list[float] = []
    timer = QTimer(window)
    timer.setInterval(20)
    timer.timeout.connect(lambda: stamps.append(time.time()))
    window.show()
    timer.start()
    deadline = time.time() + 8.0
    while time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)
        if window._engine_ready:
            break
    timer.stop()
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    check("引擎就绪前主循环最大停顿 < 250ms", bool(gaps) and max(gaps) < 0.25,
          f"{max(gaps) * 1000:.0f} ms / {len(stamps)} 次心跳" if gaps else "没有心跳采样")
    check("ipc 异步拉起不会被误判成「引擎没起来」",
          window._start_error is None and window._engine_tries == 0,
          f"{window._start_error} / 已试 {window._engine_tries} 次")
    window.close()
    QApplication.processEvents()

    # mpv_create 在系统线程/内存吃紧时会直接返回空（实测 ulimit -u 4000 用到 ~3300 就会中）。
    # 这种压力是瞬时的：要静默重试，绝不能偷偷换成子进程后端 —— 那会把第二个窗口带回来
    from lumina.mpv_lib import LibmpvPlayer
    real_launch = LibmpvPlayer.launch
    attempts: list[int] = []

    def failing_launch(self, wid=None, extra=()):
        attempts.append(len(attempts) + 1)
        self.launch_failed.emit("mpv_create 返回空（测试注入）")

    LibmpvPlayer.launch = failing_launch
    try:
        rt_config = cfg.Config(cfg.SUPPORT_DIR / "selfcheck_boot.toml")
        rt_config.set("player.backend", "libmpv")
        rt_config.set("cloud.start_mode", "manual")
        retry_win = MainWindow(rt_config)
        until = time.time() + 5.0
        while time.time() < until:
            QApplication.processEvents()
            time.sleep(0.02)
        check("libmpv 建不起 core 时留在本后端静默重试",
              retry_win.backend == "libmpv" and len(attempts) >= 3,
              f"backend={retry_win.backend} 试了 {len(attempts)} 次")
        check("重试期间没有另起 mpv 子进程",
              subprocess.run(["pgrep", "-x", "mpv"], capture_output=True).stdout.strip() == b"",
              subprocess.run(["pgrep", "-x", "mpv"], capture_output=True).stdout.decode())
        retry_win.engine.launch = lambda *a, **k: None
        retry_win.close()
        QApplication.processEvents()
    finally:
        LibmpvPlayer.launch = real_launch

    # 内嵌靠的是 --wid + vo=gpu(cocoa)；gpu-next 会自建窗口，参数层先钉住
    embed_config = cfg.Config(cfg.SUPPORT_DIR / "selfcheck_boot.toml")
    embed_config.set("player.embed_mpv", True)
    probe = MpvController(embed_config)
    probe.binary = "/opt/homebrew/bin/mpv"
    probe._opts = set()
    probe.socket_path = "/tmp/lumina-probe.sock"
    probe._wid = 456789
    probe._extra = ()
    args = probe._build_args()
    check("内嵌模式带 --wid 与 --vo=gpu",
          any(a.startswith("--wid=") for a in args) and "--vo=gpu" in args,
          [a for a in args if "wid" in a or "vo=" in a or "context" in a])
    from lumina import engine as engine_mod
    allowed = engine_mod.supported_gpu_contexts(probe.binary)
    bad = [a for a in args if a.startswith("--gpu-context=")
           and a.split("=")[1] not in allowed]
    check("不会传这个构建不支持的 gpu-context（会 fatal 退出）", not bad,
          bad or sorted(allowed))
    apis = engine_mod.gpu_apis(probe.binary)
    check("内嵌判据只认带 opengl 的构建",
          engine_mod.can_embed(probe.binary) == ("opengl" in apis), f"apis={sorted(apis)}")
    if not engine_mod.can_embed(probe.binary):
        check("不能内嵌时默认启用贴合窗口", bool(config.get("player.pin_window", True)),
              config.get("player.pin_window"))
    (cfg.SUPPORT_DIR / "selfcheck_boot.toml").unlink(missing_ok=True)


def check_launcher() -> None:
    section("启动脚本")
    # macOS 自带的是 bash 3.2，`"$(cd "$(dirname "$0") && pwd)"` 这种嵌套引号它会直接
    # 报 unexpected EOF —— 新 bash 能过，双击 .command 时却起不来，所以钉死用 /bin/bash 检
    for path in (ROOT / "start.sh", ROOT / "启动 Lumina.command"):
        out = subprocess.run(["/bin/bash", "-n", str(path)], capture_output=True, text=True)
        check(f"{path.name} 过 bash 3.2 语法检查", out.returncode == 0,
              (out.stderr or "").strip()[:140])
    res = subprocess.run(["/bin/bash", str(ROOT / "start.sh"), "--check"],
                         capture_output=True, text=True, timeout=90)
    check("--check 跑通并报出实际会启动的 App",
          res.returncode == 0 and "Lumina.app" in res.stdout, res.stdout[-160:])
    # 可分发打包必须自带隐私闸门：发出去的包里不许有凭据、用户名或编译痕迹
    portable = (ROOT / "scripts" / "make_portable.sh").read_text(encoding="utf-8")
    check("make_portable.sh 有隐私清理 + 出包前的隐私闸门",
          "清掉包里的本机痕迹" in portable and "隐私闸门" in portable
          and "__pycache__" in portable and "direct_url.json" in portable,
          [k for k in ("清掉包里的本机痕迹", "隐私闸门", "__pycache__", "direct_url.json")
           if k not in portable])
    check("闸门探针用真实凭据值而不是字段名（字段名在我们源码里，会误杀每次构建）",
          "x_storages" in portable and "Admin token" in portable
          and 'b\'"cookie"\'' not in portable)
    check("包内解释器不往 bundle 写字节码（否则清完又长回来）",
          "PYTHONDONTWRITEBYTECODE=1" in portable)
    gateway_script = (ROOT / "scripts" / "build_openlist.sh").read_text(encoding="utf-8")
    check("网关构建信息不记录本机用户名/主机名",
          "built_by" not in gateway_script, "built_by 会把 user@host 打进发布的 txt")
    # bundle 里的启动器同样要过 bash 3.2，并且必须导出内置网关的落点
    launcher = Path("/Applications/Lumina.app/Contents/MacOS/Lumina")
    if launcher.is_file():
        out = subprocess.run(["/bin/bash", "-n", str(launcher)], capture_output=True,
                             text=True)
        text = launcher.read_text(encoding="utf-8")
        check("App 启动器过 bash 3.2 语法检查", out.returncode == 0,
              (out.stderr or "").strip()[:140])
        check("App 启动器导出内置网关落点 LUMINA_APP_RES",
              "LUMINA_APP_RES" in text, text[:200])
        gateway = Path("/Applications/Lumina.app/Contents/Resources/bin/openlist")
        check("网关二进制真的编译进了 App 里", gateway.is_file() and os.access(gateway, os.X_OK),
              str(gateway))

    # 用户是从终端双击 .command 启动的，那里 LANG=zh_CN.UTF-8；QApplication 会
    # setlocale(LC_ALL, "")，于是 mpv 的 check_locale() 直接拒建 core（真实踩过，
    # 表现是「mpv_create 返回空、errno=0」）。必须在子进程里按那个环境验一遍。
    probe = (
        f"import sys; sys.path.insert(0, {str(ROOT)!r});"
        "from PySide6.QtWidgets import QApplication;"
        "from lumina import config as cfg;"
        "from lumina.mpv_lib import LibmpvPlayer, numeric_locale;"
        "app = QApplication([]);"
        "c = cfg.Config(cfg.SUPPORT_DIR / 'selfcheck_locale.toml'); c.set('player.volume', 0);"
        "p = LibmpvPlayer(c); p.launch();"
        "print('HANDLE' if p.handle else 'NONE', numeric_locale()); p.quit()"
    )
    env = {**os.environ, "LANG": "zh_CN.UTF-8", "LC_ALL": ""}
    res = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                         timeout=90, env=env)
    out = (res.stdout or "").strip()
    check("中文 locale 终端里引擎照样建得起（LC_NUMERIC 已钉成 C）",
          out.startswith("HANDLE C"), f"{out!r} rc={res.returncode} "
          f"{(res.stderr or '')[-140:]}")
    (cfg.SUPPORT_DIR / "selfcheck_locale.toml").unlink(missing_ok=True)


def check_config() -> None:
    section("配置层")
    marker = cfg.SUPPORT_DIR / "selfcheck.toml"
    store = cfg.Config(marker)
    store.set("player.hwdec", "videotoolbox")
    store.set("languages.default_subtitle_langs", ["zho", "eng"])
    reloaded = cfg.Config(marker)
    check("写盘后能读回 hwdec", reloaded.get("player.hwdec") == "videotoolbox",
          reloaded.get("player.hwdec"))
    check("列表配置能读回", reloaded.get("languages.default_subtitle_langs") == ["zho", "eng"],
          reloaded.get("languages.default_subtitle_langs"))
    check("缺失键回落到默认值", reloaded.get("translate.batch_lines") == 24,
          reloaded.get("translate.batch_lines"))
    import tomllib
    try:
        tomllib.loads(marker.read_text(encoding="utf-8"))
        parsed = True
    except Exception as exc:
        parsed = str(exc)
    check("导出的 TOML 可被标准库解析", parsed is True, str(parsed))
    marker.unlink(missing_ok=True)
    filters = cfg.media_filter()
    missing = [ext for ext in sorted(cfg.MEDIA_EXTS) if f"*{ext}" not in filters]
    check("文件对话框覆盖全部媒体扩展名（含 wmv）", not missing and "*.wmv" in filters,
          missing)


def check_subtitle_paths() -> None:
    section("字幕产物的目录与命名")
    check("文件名清洗掉路径分隔（防目录穿越）",
          "/" not in cfg.safe_name("../../etc/passwd")
          and "\\" not in cfg.safe_name("a\\b"), cfg.safe_name("../../etc/passwd"))
    check("空名/纯点号回落到 lumina",
          cfg.safe_name("") == "lumina" and cfg.safe_name("  ..  ") == "lumina",
          f"{cfg.safe_name('')}/{cfg.safe_name('  ..  ')}")
    check("超长片名截到 60 字符", len(cfg.safe_name("x" * 200)) == 60, len(cfg.safe_name("x" * 200)))
    check("中文片名原样保留", cfg.safe_name("黑道家族S02E08") == "黑道家族S02E08",
          cfg.safe_name("黑道家族S02E08"))
    check("默认根目录是软件数据目录", cfg.subtitle_root("") == cfg.SUBTITLE_DIR,
          cfg.subtitle_root(""))
    check("根目录会被 ensure_dirs 建出来", cfg.SUBTITLE_DIR.is_dir(), str(cfg.SUBTITLE_DIR))
    check("输出目录默认留空（= 用软件数据目录）",
          cfg.Config(cfg.SUPPORT_DIR / "selfcheck_paths.toml").get("subtitle.output_dir") == "",
          "默认值不是空串")
    (cfg.SUPPORT_DIR / "selfcheck_paths.toml").unlink(missing_ok=True)
    check("写不进去的目录退回默认而不是崩",
          cfg.subtitle_root("/System/Library/Fonts/nope") == cfg.SUBTITLE_DIR,
          cfg.subtitle_root("/System/Library/Fonts/nope"))
    media = cfg.WORK_DIR / "fixture" / "multi.mkv"
    check("跟随视频时根目录=视频所在目录",
          cfg.subtitle_root_for(media, "", True) == media.parent.resolve(),
          cfg.subtitle_root_for(media, "", True))
    check("不跟随时仍用软件数据目录",
          cfg.subtitle_root_for(media, "", False) == cfg.SUBTITLE_DIR,
          cfg.subtitle_root_for(media, "", False))
    check("跟随视频但视频目录写不进去时回退",
          cfg.subtitle_root_for("/System/Library/Fonts/nope.mkv", "", True) == cfg.SUBTITLE_DIR,
          cfg.subtitle_root_for("/System/Library/Fonts/nope.mkv", "", True))
    legacy = cfg.WORK_DIR / "自检老片子"
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "lumina-translated.ass").write_text("[Events]\n", encoding="utf-8")
    moved = cfg.migrate_legacy_subtitles()
    dest = cfg.SUBTITLE_DIR / "自检老片子" / "自检老片子.双语.ass"
    check("旧 work/<片名>/lumina-translated.ass 搬进 subtitles/ 并改名",
          moved >= 1 and dest.is_file() and not (legacy / "lumina-translated.ass").exists(),
          f"moved={moved} dest={dest.exists()}")
    check("迁移是幂等的，再跑不重复搬", cfg.migrate_legacy_subtitles() == 0,
          cfg.migrate_legacy_subtitles())
    import shutil
    shutil.rmtree(cfg.SUBTITLE_DIR / "自检老片子", ignore_errors=True)
    legacy.rmdir()


def check_model_dirs() -> None:
    section("模型目录（软件数据目录下单独分文件夹）")
    check("模型总目录就在软件数据目录里", cfg.MODEL_DIR.parent == cfg.SUPPORT_DIR,
          str(cfg.MODEL_DIR))
    check("转写模型单独一个 whisper/ 文件夹",
          cfg.ASR_MODEL_DIR == cfg.MODEL_DIR / "whisper", str(cfg.ASR_MODEL_DIR))
    check("翻译模型单独一个 llm/ 文件夹",
          cfg.LLM_MODEL_DIR == cfg.MODEL_DIR / "llm"
          and translate.LLM_MODEL_DIR == cfg.LLM_MODEL_DIR, str(cfg.LLM_MODEL_DIR))
    check("启动时两个子文件夹会被建出来",
          cfg.ASR_MODEL_DIR.is_dir() and cfg.LLM_MODEL_DIR.is_dir())
    legacy = cfg.MODEL_DIR / "ggml-selfcheck-fake.bin"
    legacy.write_bytes(b"x" * 2048)
    moved = cfg.migrate_legacy_models()
    dest = cfg.ASR_MODEL_DIR / legacy.name
    check("旧版直接放在 models/ 的 ggml-* 搬进 models/whisper/",
          moved >= 1 and dest.is_file() and not legacy.exists(), f"moved={moved}")
    check("模型迁移是幂等的，再跑不重复搬", cfg.migrate_legacy_models() == 0)
    dest.unlink(missing_ok=True)
    check("转写列表只认 whisper/ 里的模型",
          asr.list_models() == sorted(p.name for p in cfg.ASR_MODEL_DIR.glob("ggml-*.bin")),
          asr.list_models())


def check_app_data() -> None:
    section("应用数据的位置")
    paths = {"模型": cfg.MODEL_DIR, "转写模型": cfg.ASR_MODEL_DIR, "翻译模型": cfg.LLM_MODEL_DIR,
             "工作目录": cfg.WORK_DIR, "字幕": cfg.SUBTITLE_DIR, "缓存": cfg.CACHE_DIR,
             "配置": cfg.CONFIG_PATH, "日志": cfg.APP_LOG_PATH}
    outside = [name for name, path in paths.items()
               if path != cfg.SUPPORT_DIR and cfg.SUPPORT_DIR not in path.parents]
    check(f"{len(paths)} 类产物都在应用数据文件夹里", not outside, outside)
    check("默认字幕目录也在应用数据文件夹里", cfg.subtitle_root("") == cfg.SUBTITLE_DIR,
          cfg.subtitle_root(""))


def check_storage() -> None:
    section("存储台账与清理（路径可见、模型可删、删除锁在数据目录内）")
    import tempfile

    fake = Path(tempfile.mkdtemp(prefix="lumina-storage-"))
    names = ("SUPPORT_DIR", "CONFIG_PATH", "MODEL_DIR", "ASR_MODEL_DIR", "LLM_MODEL_DIR",
             "WORK_DIR", "LIVE_DIR", "SUBTITLE_DIR", "CACHE_DIR", "APP_LOG_PATH",
             "LOG_PATH", "LANGUAGE_HINTS_PATH")
    saved = {name: getattr(cfg, name) for name in names}
    try:
        cfg.SUPPORT_DIR = fake
        cfg.CONFIG_PATH = fake / "config.toml"
        cfg.MODEL_DIR = fake / "models"
        cfg.ASR_MODEL_DIR = cfg.MODEL_DIR / "whisper"
        cfg.LLM_MODEL_DIR = cfg.MODEL_DIR / "llm"
        cfg.WORK_DIR = fake / "work"
        cfg.LIVE_DIR = cfg.WORK_DIR / "live"
        cfg.SUBTITLE_DIR = fake / "subtitles"
        cfg.CACHE_DIR = fake / "cache"
        cfg.APP_LOG_PATH = fake / "app.log"
        cfg.LOG_PATH = fake / "lumina.log"
        cfg.LANGUAGE_HINTS_PATH = cfg.CACHE_DIR / "language_hints.json"
        for path in (cfg.ASR_MODEL_DIR, cfg.LLM_MODEL_DIR, cfg.WORK_DIR, cfg.LIVE_DIR,
                     cfg.CACHE_DIR, storage.transcript_dir(), cfg.SUBTITLE_DIR):
            path.mkdir(parents=True, exist_ok=True)
        config = cfg.Config(fake / "config.toml")

        check("体积写法可读",
              storage.human_size(0) == "0 B" and storage.human_size(1536) == "1.5 KB"
              and storage.human_size(3 * 1024 ** 3) == "3.0 GB",
              (storage.human_size(0), storage.human_size(1536)))
        check("不在家目录下的路径保持原样（不硬折成 ~）",
              storage.display_path(fake / "work").startswith("/"),
              storage.display_path(cfg.APP_LOG_PATH))
        check("家目录下的路径确实折成了 ~",
              storage.display_path(Path.home() / "Library" / "X").startswith("~/Library/X"),
              storage.display_path(Path.home() / "Library" / "X"))

        listed = {str(item.path) for _s, items in storage.sections(config) for item in items}
        need = {str(cfg.SUPPORT_DIR), str(cfg.CONFIG_PATH), str(cfg.WORK_DIR),
                str(cfg.LIVE_DIR), str(storage.transcript_dir()),
                str(storage.translation_cache_path()), str(cfg.LANGUAGE_HINTS_PATH),
                str(cfg.APP_LOG_PATH), str(cfg.LOG_PATH), str(cfg.SUBTITLE_DIR),
                str(storage.openlist_dir(config)),
                str(storage.openlist_dir(config) / "server.out")}
        check("台账把产出/缓存/日志/网关路径全部列出", need <= listed,
              sorted(need - listed))
        # 分类：开发/分发产物单独一类，别混在「网关与日志」里
        (fake / "distvenv").mkdir(parents=True, exist_ok=True)
        (fake / "distvenv" / "python").write_bytes(b"0" * 512)
        categories = [name for name, _items in storage.sections(config)]
        check("台账按用途分类，开发产物单独一类「构建与分发」",
              categories == ["软件数据根", "字幕产出", "缓存与标记", "网关与日志",
                             "构建与分发"], categories)
        build_labels = {item.label for name, items in storage.sections(config)
                        if name == "构建与分发" for item in items}
        check("「构建与分发」列出网关构建缓存与可分发打包运行时",
              {"网关构建缓存", "可分发打包运行时"} <= build_labels, build_labels)
        check("网关那一类不再混着构建缓存",
              all("构建" not in item.label
                  for name, items in storage.sections(config)
                  if name == "网关与日志" for item in items))
        count, _size = storage.preview("portable_venv", config)
        check("可分发打包运行时能单独清（数得出里面几个文件）", count == 1, count)
        check("可分发运行时与构建缓存都不进一键清理",
              "portable_venv" not in storage.CLEAN_KEYS
              and storage.BUILD_CACHE_KEY not in storage.CLEAN_KEYS)
        storage.clean("portable_venv", config)
        check("清完可分发运行时，台账里那一行就消失",
              not any(item.label == "可分发打包运行时"
                      for name, items in storage.sections(config)
                      if name == "构建与分发" for item in items))
        check("每个可清理项都有对应的清理实现",
              all(item.clean in storage.ALL_CLEAN_KEYS
                  for _s, items in storage.sections(config) for item in items
                  if item.clean))
        check("字幕产物不在自动清理范围里（只能进字幕库手删）",
              "subtitles" not in storage.CLEAN_KEYS
              and all(item.clean == "" for _s, items in storage.sections(config)
                      for item in items if item.path == cfg.SUBTITLE_DIR))

        # 递归体积
        (cfg.LIVE_DIR / "a.ass").write_text("hello", encoding="utf-8")
        (cfg.WORK_DIR / "b.wav").write_bytes(b"z" * 4096)
        check("目录体积递归求和",
              storage.path_size(cfg.WORK_DIR) >= 4096,
              storage.path_size(cfg.WORK_DIR))

        # 模型台账
        (cfg.ASR_MODEL_DIR / "ggml-big.bin").write_bytes(b"1" * 400_000)
        (cfg.ASR_MODEL_DIR / "ggml-small-q5_1.bin").write_bytes(b"1" * 100_000)
        (cfg.LLM_MODEL_DIR / "keep.gguf").write_bytes(b"1" * 200_000)
        (cfg.LLM_MODEL_DIR / "half.gguf.part").write_bytes(b"1" * 50_000)
        config.set("asr.model", "ggml-big.bin")
        config.set("translate.llama_model", "not-installed.gguf")
        rows = storage.installed_models(config)
        check("模型台账只认 whisper/*.bin 与 llm/*.gguf",
              sorted(row.name for row in rows) ==
              ["ggml-big.bin", "ggml-small-q5_1.bin", "keep.gguf"],
              [row.name for row in rows])
        check("在用的模型标出用途",
              any(row.name == "ggml-big.bin" and "转写在用" in row.usage for row in rows),
              [(row.name, row.usage) for row in rows])
        check("没下完的 .part 单独成项、不算模型",
              [p.name for p in storage.leftover_parts(config)] == ["half.gguf.part"])

        # 删除防护：越界、错类型、不存在
        for bad, why in (("../config.toml", "越出模型目录"),
                         ("sub/keep.gguf", "子目录里的不算"),
                         ("notes.txt", "不是模型文件"),
                         ("gone.gguf", "文件不存在")):
            try:
                storage.delete_model("llm", bad, config)
                check(f"拒绝删除 {why}", False, "居然删成功了")
            except storage.StorageError as exc:
                check(f"拒绝删除 {why}", True, str(exc))
        check("防护没有误删：模型目录还是完整的",
              sorted(p.name for p in cfg.LLM_MODEL_DIR.iterdir())
              == ["half.gguf.part", "keep.gguf"],
              [p.name for p in cfg.LLM_MODEL_DIR.iterdir()])

        note = storage.delete_model("asr", "ggml-big.bin", config)
        check("删掉的转写模型真的没了，配置改用替补",
              not (cfg.ASR_MODEL_DIR / "ggml-big.bin").exists()
              and config.get("asr.model") == "ggml-small-q5_1.bin", note)
        config.set("translate.llama_model", "keep.gguf")
        note2 = storage.delete_model("llm", "keep.gguf", config)
        check("删掉唯一在用的翻译模型时说明没有替补，而不是假装还能用",
              "已没有同类模型" in note2 and not (cfg.LLM_MODEL_DIR / "keep.gguf").exists(),
              note2)

        # 清理：预览不动手
        (cfg.WORK_DIR / "clip").mkdir()
        (cfg.WORK_DIR / "clip" / "source-1.srt").write_text("1", encoding="utf-8")
        outside = fake / "..-keep-me.wav"
        outside.write_bytes(b"0" * 10)
        sneaky = cfg.WORK_DIR / "escape.wav"
        sneaky.symlink_to(outside.resolve())
        count, size = storage.preview("work", config)
        check("预览先数一遍：临时音频、抽取字幕与软链接都算进去，但不顺着软链接删外面",
              count == 3 and size >= 4096 and outside.exists(), (count, size))
        removed, freed = storage.clean("work", config)
        check("清理 work 会跳过 live（实时字幕有自己的清理项）",
              (cfg.LIVE_DIR / "a.ass").exists() and removed == 3,
              (removed, sorted(p.name for p in cfg.LIVE_DIR.iterdir())))
        check("清理不顺着软链接删外面的文件", outside.exists() and not sneaky.exists())
        check("清理后空目录被收掉", not (cfg.WORK_DIR / "clip").exists(),
              [p.name for p in cfg.WORK_DIR.iterdir()])
        check("清理 live 只删临时字幕",
              storage.clean("live", config)[0] == 1
              and not any(cfg.LIVE_DIR.iterdir()))

        (storage.transcript_dir() / "abc.json").write_text("{}", encoding="utf-8")
        (storage.transcript_dir() / "keep.txt").write_text("x", encoding="utf-8")
        storage.clean("transcripts", config)
        check("清转写标记只删 json，别的文件不动",
              not (storage.transcript_dir() / "abc.json").exists()
              and (storage.transcript_dir() / "keep.txt").exists())
        check("没有标记时清理返回 0", storage.clean("transcripts", config) == (0, 0))

        storage.translation_cache_path().write_text("x", encoding="utf-8")
        check("清翻译缓存只删那一个 jsonl",
              storage.clean("translations", config) == (1, 1)
              and not storage.translation_cache_path().exists())

        cfg.set_media_language_hint("某部片", "ja")
        check("语言台账文件写出来了", cfg.LANGUAGE_HINTS_PATH.is_file())
        storage.clean("language_hints", config)
        check("清语言台账同时扔掉内存副本",
              not cfg.LANGUAGE_HINTS_PATH.exists()
              and cfg.media_language_hint("某部片") == "auto",
              cfg.media_language_hint("某部片"))

        cfg.APP_LOG_PATH.write_text("lots of noise", encoding="utf-8")
        cfg.LOG_PATH.write_text("mpv noise", encoding="utf-8")
        storage.clean("logs", config)
        check("清日志：应用日志清空、播放器日志删掉",
              cfg.APP_LOG_PATH.exists() and cfg.APP_LOG_PATH.stat().st_size == 0
              and not cfg.LOG_PATH.exists())

        kept = sorted(p.name for p in cfg.MODEL_DIR.rglob("*") if p.is_file())
        storage.clean("parts", config)
        check("清断点只删 .part，模型目录里的其余文件不动",
              kept == ["ggml-small-q5_1.bin", "half.gguf.part"]
              and sorted(p.name for p in cfg.MODEL_DIR.rglob("*") if p.is_file())
              == ["ggml-small-q5_1.bin"],
              [p.name for p in cfg.MODEL_DIR.rglob("*")])

        try:
            storage.clean("subtitles", config)
            check("不认识清理项时直接报错，不含糊删东西", False)
        except storage.StorageError as exc:
            check("不认识清理项时直接报错，不含糊删东西", True, str(exc))

        # 一键清理不碰模型与字幕
        (cfg.WORK_DIR / "again.wav").write_bytes(b"0" * 2048)
        (cfg.SUBTITLE_DIR / "片子.原文.srt").write_text("1", encoding="utf-8")
        count, size = storage.clean_all(config, dry_run=True)
        check("一键清理的预览数得出待删文件", count >= 1, (count, size))
        storage.clean_all(config)
        check("一键清理不动字幕", (cfg.SUBTITLE_DIR / "片子.原文.srt").exists())
        check("一键清理不动已下好的模型",
              (cfg.ASR_MODEL_DIR / "ggml-small-q5_1.bin").exists())
    finally:
        for name, value in saved.items():
            setattr(cfg, name, value)
        shutil.rmtree(fake, ignore_errors=True)


def check_cloud_lifecycle() -> None:
    section("OpenList 网关与软件同生同死")
    from lumina import cloud

    config = cfg.Config(cfg.SUPPORT_DIR / "selfcheck_cloud.toml")
    client = cloud.OpenList(config)
    check("默认随软件启动（start_mode=auto）",
          cfg.DEFAULTS["cloud"]["start_mode"] == "auto"
          and client.autostart_wanted(), config.get("cloud.start_mode"))
    config.set("cloud.start_mode", "manual")
    check("手动模式不再自动带起网关", not client.autostart_wanted())
    check("手动模式 + 不是我们拉起的进程 → 关窗不动它",
          not client.should_stop_on_quit())

    class FakeProc:
        def __init__(self):
            self.terminated = False
            self.waited = 0.0

        def poll(self):
            return None if not self.terminated else 0

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            self.waited = timeout or 0.0

        def kill(self):
            self.terminated = True

    proc = FakeProc()
    client._proc = proc
    check("只要本进程拉起过子进程，无论什么模式关窗都要收掉",
          client.should_stop_on_quit())
    client.healthy = lambda: False          # 不真去探测端口
    client.pids = lambda: []
    started = time.time()
    client.shutdown_for_quit()
    check("关窗停机走快速路径（不把人卡在退出上）",
          proc.terminated and client._proc is None
          and time.time() - started < 2.0, time.time() - started)
    config.set("cloud.start_mode", "auto")
    client._proc = None
    client._adopted = True          # 启动时端口上已经有一份（用户自己跑的）
    check("启动时就存在的网关，退出时不去抢着停", not client.should_stop_on_quit())
    client._adopted = False
    check("自己带起来的网关退出时必须收掉（自动模式）", client.should_stop_on_quit())

    # 启动等待和关窗撞车：等待循环里再摸 self._proc 就是 None.poll()，
    # 线程炸在 stderr，用户只看到「开了就退」+ 一个没人管的网关
    race = cloud.OpenList(config)
    race.healthy = lambda: False
    race.pids = lambda: []
    race.binary = lambda: "/bin/echo"
    fake = FakeProc()
    real_popen = cloud.subprocess.Popen
    errors: list[BaseException] = []

    def run_race() -> None:
        try:
            race.ensure_running()
        except BaseException as exc:  # noqa: BLE001 —— 自检就是要抓住任何异常
            errors.append(exc)

    cloud.subprocess.Popen = lambda *a, **k: fake
    thread = threading.Thread(target=run_race)
    try:
        thread.start()
        time.sleep(0.2)          # 让线程先进到等待循环
        race.shutdown_for_quit()  # 模拟「刚带起来就关窗」
        thread.join(3.0)
    finally:
        cloud.subprocess.Popen = real_popen
    check("启动等待中被人关窗：线程不炸、立刻收手、子进程照样收掉",
          not errors and not thread.is_alive() and fake.terminated,
          errors[:1] or ("线程还活着" if thread.is_alive() else ""))
    # 关窗后 autostart 线程还会接着要 token → 又会走一遍 ensure_running；
    # 不设闸门的话这里能把网关第二次拉起来，变成一个没人管的孤儿进程
    spawned_again = []
    cloud.subprocess.Popen = lambda *a, **k: spawned_again.append(a) or fake
    try:
        try:
            race.ensure_running()
            blocked = ""
        except Exception as exc:      # noqa: BLE001 —— 逃逸的异常本身就是要抓的东西
            blocked = f"{type(exc).__name__}: {exc}"
    finally:
        cloud.subprocess.Popen = real_popen
    check("退出路径走过之后绝不再带起网关",
          not spawned_again and blocked.startswith("CloudError") and "退出" in blocked,
          blocked or "又拉起来一次")

    # 换血判定：端口上跑的不是我们要用的那一份时，自动模式要切过来
    # （用单独的实例，别把上面那组 fake 方法污染到后面的解析测试）
    mig = cloud.OpenList(config)
    mig._proc = None
    mig.healthy = lambda: True
    mig.running_binary = lambda: "/somewhere/old/openlist"
    mig.binary = lambda: "/Applications/Lumina.app/Contents/Resources/bin/openlist"
    check("跑的是别处那份旧二进制 → 判定要换血", mig.migration_needed() is True)
    mig.running_binary = lambda: "/Applications/Lumina.app/Contents/Resources/bin/openlist"
    check("跑的已经是我们要用的那一份 → 不动它", mig.migration_needed() is False)
    mig.running_binary = lambda: ""
    check("查不到在跑的进程就不瞎换血", mig.migration_needed() is False)
    mig.running_binary = lambda: "/somewhere/old/openlist"
    config.set("cloud.start_mode", "manual")
    check("手动模式绝不换血（那是别人的服务）", mig.migration_needed() is False)
    config.set("cloud.start_mode", "auto")
    mig.healthy = lambda: False
    check("端口上没人 → 不需要换血，直接起", mig.migration_needed() is False)
    check("源码直跑时仓库 dist 里那份也在候选里",
          any("dist/Lumina.app/Contents/Resources/bin/openlist" in str(path)
              for path in cloud.bundle_binaries()),
          [str(path) for path in cloud.bundle_binaries()])

    def boom() -> None:
        raise cloud.CloudError("找不到 openlist，请先安装")

    client.ensure_running = boom          # 别真去把本机网关拉起来，这里只测失败路径
    ok, message = client.autostart()
    check("自动模式启动失败会带回原因，不静默", not ok and "找不到 openlist" in message,
          message)

    # 二进制查找顺序：App 内置优先，外部装的只作退路
    import tempfile
    fake_root = Path(tempfile.mkdtemp(prefix="lumina-bundle-"))
    bundled = fake_root / "bin" / "openlist"
    bundled.parent.mkdir(parents=True, exist_ok=True)
    bundled.write_text("#!/bin/sh\n", encoding="utf-8")
    bundled.chmod(0o755)
    old_res = os.environ.get(cloud.APP_RES_ENV)
    os.environ[cloud.APP_RES_ENV] = str(fake_root)
    try:
        check("设了 LUMINA_APP_RES 就用 bundle 里那份网关",
              Path(client.binary()).resolve() == bundled.resolve(), client.binary())
        check("来源标成「App 内置」", client.binary_origin() == "App 内置",
              client.binary_origin())
        # 配置里显式指定的路径仍然优先（他要能自己换）
        other = fake_root / "mine" / "openlist"
        other.parent.mkdir(parents=True, exist_ok=True)
        other.write_text("#!/bin/sh\n", encoding="utf-8")
        config.set("cloud.openlist_binary", str(other))
        check("设置里指定的网关路径优先于内置", Path(client.binary()) == other,
              client.binary())
        check("来源标成「设置里指定的路径」",
              client.binary_origin() == "设置里指定的路径", client.binary_origin())
        config.set("cloud.openlist_binary", "")
        ledger = {str(item.path) for _s, items in storage.sections(config) for item in items}
        check("存储台账列出 App 内置网关的落点",
              str(cloud.bundle_binaries()[0]) in ledger, sorted(ledger)[-4:])
        fake_legacy = fake_root / "旧版副本" / "openlist"
        fake_legacy.parent.mkdir(parents=True, exist_ok=True)
        saved_present, saved_path = (storage.gateway_legacy_present,
                                     storage.gateway_legacy_path)
        try:
            storage.gateway_legacy_path = lambda: fake_legacy
            storage.gateway_legacy_present = lambda: False
            ledger = {str(item.path) for _s, items in storage.sections(config)
                      for item in items}
            check("旧副本已删掉时不再列一行假路径", str(fake_legacy) not in ledger)
            fake_legacy.write_text("#!/bin/sh\n", encoding="utf-8")
            storage.gateway_legacy_present = lambda: fake_legacy.is_file()
            ledger = {str(item.path) for _s, items in storage.sections(config)
                      for item in items}
            check("旧副本还在时列出来并给清理入口",
                  str(fake_legacy) in ledger
                  and any(item.clean == storage.GATEWAY_LEGACY_KEY
                          for _s, items in storage.sections(config) for item in items
                          if str(item.path) == str(fake_legacy)))
        finally:
            storage.gateway_legacy_present = saved_present
            storage.gateway_legacy_path = saved_path
        check("旧版网关不在一键清理范围内（可能正在跑）",
              storage.GATEWAY_LEGACY_KEY not in storage.CLEAN_KEYS
              and storage.GATEWAY_LEGACY_KEY in storage.ALL_CLEAN_KEYS)
    finally:
        if old_res is None:
            os.environ.pop(cloud.APP_RES_ENV, None)
        else:
            os.environ[cloud.APP_RES_ENV] = old_res
        shutil.rmtree(fake_root, ignore_errors=True)
    config.path.unlink(missing_ok=True)


def check_cloud_multi_storage() -> None:
    section("多网盘：任意 OpenList 存储都能浏览与播放")
    import http.server
    import json
    import socketserver
    from lumina import cloud

    class Handler(http.server.BaseHTTPRequestHandler):
        calls: list = []
        replies: dict = {}

        def _handle(self, method: str, body: dict) -> None:
            Handler.calls.append((method, self.path.split("?")[0], body))
            data = Handler.replies.get(self.path.split("?")[0],
                                       {"code": 200, "message": "success", "data": {}})
            raw = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:      # noqa: N802
            self._handle("GET", {})

        def do_POST(self) -> None:     # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            self._handle("POST", json.loads(self.rfile.read(length) or b"{}"))

        def log_message(self, *args) -> None:
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    config = cfg.Config(cfg.SUPPORT_DIR / "selfcheck_cloud.toml")
    client = cloud.OpenList(config)
    client.port = server.server_address[1]
    client.token = lambda force=False: "stub-token"
    try:
        Handler.replies["/api/admin/storage/list"] = {"code": 200, "data": {"content": [
            {"mount_path": "/115", "driver": "115 Cloud", "status": "work", "order": 0},
            {"mount_path": "/ali", "driver": "Aliyundrive Open", "status": "work", "order": 1},
            {"mount_path": "/off", "driver": "S3", "status": "disabled", "order": 2},
        ]}}
        mounts = client.mounts()
        check("列出所有可用挂载（115 只是其中一个）",
              [m["mount"] for m in mounts] == ["/115", "/ali"], mounts)
        check("停用的存储不算挂载", all(m["mount"] != "/off" for m in mounts))

        source = client.source("/ali/movie.mkv", "movie.mkv")
        check("非 115 挂载生成的是同一条 WebDAV 路径",
              source.uri.endswith("/dav/ali/movie.mkv") and source.origin == "cloud/ali",
              (source.uri, source.origin))
        check("115 的媒体键保持原样（切过去不重转）",
              client.source("/115/a.mkv").key == "115:/115/a.mkv")

        Handler.calls.clear()
        client.create_storage("Local", "local/", {"root_folder_path": "/tmp/x"}, "note")
        method, path, body = Handler.calls[-1]
        check("添加存储打到 /api/admin/storage/create",
              method == "POST" and path == "/api/admin/storage/create", (method, path))
        check("挂载路径归一化、addition 是 JSON 字符串",
              body.get("mount_path") == "/local"
              and json.loads(body.get("addition") or "{}")["root_folder_path"] == "/tmp/x",
              body)

        Handler.replies["/api/admin/driver/list"] = {"code": 200, "data": {
            "Local": {"additional": [{"name": "root_folder_path", "type": "string"}]}}}
        check("驱动元数据能取到（建存储表单靠它）",
              "Local" in client.driver_info())
    finally:
        server.shutdown()
        config.path.unlink(missing_ok=True)


def check_i18n() -> None:
    section("界面语言：词表 / 占位符 / 产物后缀 / 热切换")
    from PySide6.QtWidgets import QLabel
    from lumina import i18n, naming

    def has_cjk(text: str) -> bool:
        return any("\u4e00" <= ch <= "\u9fff" for ch in text)

    bad = [key for key, value in i18n.EN.items() if has_cjk(value)]
    check("英文词表里没有残留中文", not bad, bad[:4])
    check("语言偏好解析（zh/en 显式，auto 有结果）",
          i18n.resolve("zh") == "zh" and i18n.resolve("en") == "en"
          and i18n.resolve("auto") in ("zh", "en"))

    i18n.set_language("zh")
    check("中文界面原样返回", i18n.t("播放") == "播放")
    i18n.set_language("en")
    check("英文界面查表", i18n.t("播放") == "Playback")
    check("占位符按参数填充",
          i18n.t("当前实际解码：{}", "videotoolbox") == "Actual decoder: videotoolbox")
    check("漏翻的键原样回退并记账",
          i18n.t("没有这个词条") == "没有这个词条"
          and "没有这个词条" in i18n.missing())

    label = QLabel("")
    texts = panels._Texts(label)
    texts.label(label, "播放")
    i18n.set_language("zh")
    i18n.retranslate()
    zh_text = label.text()
    i18n.set_language("en")
    i18n.retranslate()
    check("控件文字随语言热切换", (zh_text, label.text()) == ("播放", "Playback"),
          (zh_text, label.text()))

    i18n.set_language("zh")
    check("中文产物后缀", naming.bilingual_name("片") == "片.双语.ass"
          and naming.translated_name("片", "中文") == "片.中文译文.srt")
    check("挂回字幕时两种后缀都认",
          {p.name for p in naming.bilingual_candidates("片")}
          == {"片.双语.ass", "片.bilingual.ass"}
          and {p.name for p in naming.original_candidates("片")}
          == {"片.原文.srt", "片.original.srt"})
    i18n.set_language("en")
    check("英文产物后缀",
          naming.bilingual_name("film") == "film.bilingual.ass"
          and naming.translated_name("film", "中文") == "film.Chinese.translated.srt")
    check("英文界面也接受中文命名（旧产物不改名）",
          "film.中文译文.srt" in naming.translated_globs("film", "中文"))
    i18n.set_language("zh")
    check("切回中文后产物后缀复原", naming.bilingual_name("film") == "film.双语.ass")


def check_live_stream_budget() -> None:
    section("云端实时翻译的读流预算（mpv 预读 × 缓存 × 码率）")
    from lumina.stream_proxy import RangeCacheProxy
    from lumina.media import MediaSource
    from lumina.engine import MpvController

    proxy = RangeCacheProxy(MediaSource(uri="http://x/y", display_name="y", key="k"),
                            cache_mb=128)
    proxy._total = 2_737_893_696          # 用户那部 2.55 GiB 的片子
    wide = proxy.max_lead_seconds(4500.0)   # 75 分钟 → 0.61 MB/s
    tight = proxy.max_lead_seconds(1200.0)  # 20 分钟 → 2.28 MB/s
    check("按码率算预读上限：慢码率给到上百秒，快码率压到几十秒",
          90 < wide < 130 and 20 < tight < 40, (round(wide, 1), round(tight, 1)))
    check("算不出码率时不加这层限制（沿用设置值）",
          proxy.max_lead_seconds(0.0) == 0.0 and proxy.max_lead_seconds(-1) == 0.0)

    class StubEngine:
        def __init__(self):
            self.sets: dict[str, object] = {}

        def get(self, name, default=None, **_kw):
            return {"demuxer-max-bytes": 120 * 1024 * 1024,
                    "demuxer-max-back-bytes": 60 * 1024 * 1024,
                    "demuxer-readahead-secs": 30}.get(name, default)

        def set(self, name, value):
            self.sets[name] = value

    stub = StubEngine()
    MpvController._apply_read_ahead(stub, 16)
    check("走单流代理时压小 mpv 预读（默认会往前冲上百 MB，把 ASR 挤出缓存）",
          stub.sets.get("demuxer-max-bytes") == "16MiB"
          and stub.sets.get("demuxer-readahead-secs") == "5", stub.sets)
    back = StubEngine()
    MpvController._apply_read_ahead(back, 16)
    MpvController._apply_read_ahead(back, None)
    check("换回本地/直连播放要恢复 mpv 原本的预读设置",
          back.sets.get("demuxer-max-bytes") == 120 * 1024 * 1024, back.sets)

    config = cfg.Config(cfg.SUPPORT_DIR / "selfcheck_lead.toml")
    pipe = SubtitlePipeline(StubEngine(), config)
    pipe.cloud_live = True
    config.set("asr.live_lead", 300.0)          # 用户选了「整片（尽可能超前）」
    config.set("cloud.live_lead_max", 60)
    check("云端实时先吃 60 秒上限", pipe._live_lead(60.0)[0] == 60.0, pipe._live_lead(60.0))
    pipe.cloud_lead_cap = 28.4                  # 高码率片子：缓存只装得下 28 秒
    lead, unlimited = pipe._live_lead(60.0)
    check("再按缓存能装下的秒数夹一层，且绝不退化成不限超前",
          abs(lead - 28.4) < 0.01 and unlimited is False, (lead, unlimited))
    pipe.cloud_live = False
    check("本地实时不受云端上限影响", pipe._live_lead(60.0)[0] == 300.0)
    pipe.cloud_lead_cap = 0.0

    check("流被掐断不会被说成「没有音轨」",
          asr.is_stream_break("[http] Stream ends prematurely at 213609259, should be 2737893696")
          and asr.is_stream_break("Error during demuxing: Input/output error")
          and not asr.is_stream_break("does not contain any stream"))
    config.set("translate.engine", "llama")
    config.set("translate.live_llama_model", "zz-没有这个模型.gguf")
    problem = translate.model_problem(config, live=True)
    check("实时模型没就绪时给出可执行的说明（而不是静默不翻）",
          "没下载" in problem and "下载实时模型" in problem, problem)
    part = cfg.LLM_MODEL_DIR / "zz-半截.gguf.part"
    part.write_bytes(b"0" * 2048)
    config.set("translate.live_llama_model", "zz-半截.gguf")
    half = translate.model_problem(config, live=True)
    part.unlink(missing_ok=True)
    check("只下了一半的模型说清是断点，并告诉怎么接着下",
          "只下了一半" in half and "0 MB 断点" in half, half)
    config.set("translate.engine", "openai")
    check("在线引擎不需要本地模型，不该拦", translate.model_problem(config, live=True) == "")
    config.path.unlink(missing_ok=True)


def check_langtags() -> None:
    section("语言标签归一")
    expanded = langtags.expand_for_mpv(["zh", "en"])
    check("中文展开覆盖 zho/chi/cmn", {"zh", "zho", "chi", "cmn"} <= set(expanded.split(",")), expanded)
    check("chi 与 zh 判为同一种语言", langtags.same_lang("chi", "zh"))
    check("zh-TW 基标签是 zh", langtags.base_of("zh-TW") == "zh", langtags.base_of("zh-TW"))
    check("zho 归一到 zh", langtags.base_of("zho") == "zh")
    check("偏好排序取最优", langtags.best_index(["eng", "zho"], ["eng", "zh"]) == 0)
    check("未标注不当匹配", langtags.rank_of("und", ["zh"]) == -1)
    check("翻译目标码中文带地区", langtags.to_translate_code("chi") == "zh-CN")


def check_subtitles() -> None:
    section("字幕解析与生成")
    srt = "1\n00:00:01,000 --> 00:00:03,000\nHello\nworld\n\n2\n00:00:04,500 --> 00:00:06,000\nSecond\n"
    cues = subtitles.parse_srt(srt)
    check("SRT 条数", len(cues) == 2, len(cues))
    check("SRT 多行保留", cues[0].lines == ["Hello", "world"], cues[0].lines)
    check("SRT 时间解析", abs(cues[1].start - 4.5) < 1e-6, cues[1].start)
    vtt = ("WEBVTT\n\n00:00:01.000 --> 00:00:02.500\nalign:start\n- 喂\n\n"
           "00:00:03.000 --> 00:00:04.000\n<i>下一句</i>\n")
    vtt_cues = subtitles.parse_vtt(vtt)
    check("VTT 条数", len(vtt_cues) == 2, len(vtt_cues))
    check("VTT 去标签", vtt_cues[1].plain() == "下一句", vtt_cues[1].plain())
    ass = ("[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\n"
           "Format: Name, Fontname\nStyle: Default,Arial\n\n[Events]\n"
           "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
           "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,{\\i1}第一句{\\i0}\n"
           "Dialogue: 0,0:00:02.50,0:00:03.50,Default,,0,0,0,,第二句\\N第三行\n")
    ass_cues = subtitles.parse_ass(ass)
    check("ASS 条数", len(ass_cues) == 2, len(ass_cues))
    check("ASS 剥 override", ass_cues[0].plain() == "第一句", ass_cues[0].plain())
    check("ASS 的 \\N 转换行", ass_cues[1].lines == ["第二句", "第三行"], ass_cues[1].lines)

    out = FIXTURE_DIR / "bilingual.ass"
    originals = [c.plain() for c in ass_cues]
    subtitles.write_ass(out, ass_cues, subtitles.AssStyle(),
                        translated=["One", "Two three"], position="below", keep_original=True)
    text = out.read_text(encoding="utf-8")
    dialogues = [ln for ln in text.splitlines() if ln.startswith("Dialogue:")]
    check("双语每条写两行 Dialogue", len(dialogues) == 2 * len(ass_cues), len(dialogues))
    check("译文用独立样式", sum(1 for ln in dialogues if ",Trans," in ln) == 2)

    def margin_of(line: str) -> int:
        parts = line.split(",")
        return int(parts[7])

    main_lines = [ln for ln in dialogues if ",Main," in ln]
    trans_lines = [ln for ln in dialogues if ",Trans," in ln]
    check("below 布局下原文行边距更大（位置更高）",
          all(margin_of(m) > margin_of(t) for m in main_lines for t in trans_lines),
          [(margin_of(m), margin_of(t)) for m, t in zip(main_lines, trans_lines)])
    reparsed = subtitles.parse_ass(text)
    check("生成的 ASS 能被自己解析回同样句数", len(reparsed) == 2 * len(ass_cues), len(reparsed))
    check("生成内容不含裸花括号", "{" not in text.split("[Events]")[1])
    empty = subtitles.Cue(0.0, 1.0, ["-"])
    check("纯标点判空", empty.is_empty())

    long = subtitles.split_long_cues(
        [subtitles.Cue(0.0, 8.0, ["The harbor lights are still on, nobody told him the ferry "
                                  "stopped, he waited until the fog came in."])], limit=44)
    check("英文长句被切成多块", len(long) >= 3, [c.plain() for c in long])
    check("每块不超字符上限", all(len(c.plain()) <= 44 for c in long),
          [len(c.plain()) for c in long])
    check("切块时间单调递增", all(long[i].end <= long[i + 1].end + 1e-6
                             for i in range(len(long) - 1)))
    check("切块覆盖原时长", abs(long[-1].end - 8.0) < 0.01, long[-1].end)
    zh = subtitles.split_long_cues(
        [subtitles.Cue(0.0, 6.0, ["港口的灯还亮着，没人告诉他渡轮已经停了，他一直等到雾涌进来。"])], limit=18)
    check("中文按标点切", all(len(c.plain()) <= 18 for c in zh), [c.plain() for c in zh])
    check("切块后文本合起来等于原文",
          "".join(c.plain() for c in zh).replace(" ", "") ==
          "港口的灯还亮着，没人告诉他渡轮已经停了，他一直等到雾涌进来。".replace(" ", ""),
          [c.plain() for c in zh])

    long_zh = "这是一条特别长的中文字幕，" * 6
    wrapped_zh, used_zh = subtitles.fit_ass_text(long_zh, 50)
    check("长中文字幕折成最多两行", len(wrapped_zh) <= 2, (len(wrapped_zh), used_zh))
    check("折行后每行不超过安全宽度",
          all(subtitles.ass_text_width(line, used_zh) <= subtitles.ASS_SAFE_WIDTH + 1
              for line in wrapped_zh),
          [(line, round(subtitles.ass_text_width(line, used_zh))) for line in wrapped_zh])
    long_en = ("This is a very long subtitle line that should be wrapped without "
               "overflowing the video frame. " * 2)
    wrapped_en, used_en = subtitles.fit_ass_text(long_en, 50)
    check("长英文字幕折成最多两行", len(wrapped_en) <= 2, (len(wrapped_en), used_en))
    long_ass = FIXTURE_DIR / "long.ass"
    subtitles.write_ass(long_ass, [subtitles.Cue(1.0, 7.0, [long_zh])], subtitles.AssStyle(),
                        translated=["这是一条同样很长的译文，" * 6], keep_original=True)
    long_written = long_ass.read_text(encoding="utf-8")
    long_dialogues = [ln for ln in long_written.splitlines() if ln.startswith("Dialogue:")]
    check("长字幕写进 ASS 时自动折行或缩小字号",
          any("\\N" in ln or "\\fs" in ln for ln in long_dialogues),
          long_dialogues[:2])
    dup_ass = FIXTURE_DIR / "dup.ass"
    subtitles.write_ass(dup_ass, [subtitles.Cue(1.0, 2.0, ["同一句话"])],
                        subtitles.AssStyle(), translated=["同一句话"], keep_original=True)
    dup_dialogues = [ln for ln in dup_ass.read_text(encoding="utf-8").splitlines()
                     if ln.startswith("Dialogue:")]
    check("译文与原文相同时不重复写第二条", len(dup_dialogues) == 1, dup_dialogues)
    near_ass = FIXTURE_DIR / "near_echo.ass"
    subtitles.write_ass(near_ass,
                        [subtitles.Cue(0.0, 2.0, ["何するの"])],
                        subtitles.AssStyle(), translated=["何するので"], keep_original=True)
    near_dialogues = [ln for ln in near_ass.read_text(encoding="utf-8").splitlines()
                      if ln.startswith("Dialogue:")]
    check("译文只差个别字词时也不重复写第二条",
          len(near_dialogues) == 1, near_dialogues)
    overlap_ass = FIXTURE_DIR / "overlap.ass"
    subtitles.write_ass(overlap_ass,
                        [subtitles.Cue(0.0, 2.0, ["第一句"]),
                         subtitles.Cue(1.5, 3.0, ["第二句"])],
                        subtitles.AssStyle(), keep_original=True)
    overlap_cues = subtitles.parse_ass(overlap_ass.read_text(encoding="utf-8"))
    check("快语速造成的轻微重叠不会两句同屏",
          len(overlap_cues) == 2 and overlap_cues[0].end <= overlap_cues[1].start + 0.01,
          [(round(c.start, 2), round(c.end, 2)) for c in overlap_cues])


class StubTranslator(translate.Translator):
    name = "stub"
    model_label = "stub"

    def __init__(self, short=False):
        self.calls = 0
        self.short = short

    def translate(self, texts, target, source=None, context=None):
        self.calls += 1
        out = [f"«{t}»" for t in texts]
        return out[:-1] if self.short and len(out) > 1 else out


def check_translate() -> None:
    section("翻译层")
    cache_path = cfg.CACHE_DIR / "selfcheck-cache.jsonl"
    cache_path.unlink(missing_ok=True)
    cache = translate.Cache(path=cache_path, enabled=True)
    stub = StubTranslator()
    first = pipeline_translate(cache, stub, ["A b", "C d"])
    second = pipeline_translate(cache, stub, ["A b", "C d"])
    check("首批调用了一次接口", stub.calls == 1, stub.calls)
    check("第二批全部命中缓存", stub.calls == 1, stub.calls)
    check("缓存内容正确", second == ["«A b»", "«C d»"], second)
    check("缓存真的落盘", cache_path.exists() and cache_path.stat().st_size > 0)
    raised = False
    try:
        pipeline_translate(cache, StubTranslator(short=True), ["x", "y", "z"])
    except PipelineError:
        raised = True
    check("返回条数不符时报错而不是错位", raised)
    cache_path.unlink(missing_ok=True)

    check("日文原样返回会被识别成没翻译",
          translate.looks_untranslated("今日は", "今日は", "ja", "zh-CN")
          and not translate.looks_untranslated("先生", "先生", "ja", "zh-CN")
          and not translate.looks_untranslated("OK", "OK", "en", "zh-CN"),
          [translate.looks_untranslated(t, t, "ja", "zh-CN")
           for t in ("今日は", "先生", "OK")])

    class EchoOrGood(translate.Translator):
        name = "test"
        model_label = "test"

        def __init__(self, echo: bool):
            self.echo = echo
            self.calls = 0

        def translate(self, texts, target, source=None, context=None):
            self.calls += 1
            return list(texts) if self.echo else ["今天" for _ in texts]

    echo_cache = translate.Cache(path=cfg.CACHE_DIR / "selfcheck-echo-cache.jsonl",
                                 enabled=True)
    echo_cache.clear()
    echo = EchoOrGood(echo=True)
    echoed = pipeline_translate(echo_cache, echo, ["今日は"])
    good = EchoOrGood(echo=False)
    fixed = pipeline_translate(echo_cache, good, ["今日は"])
    check("原样返回的日文不写缓存，下一轮会真的重翻",
          echoed == ["今日は"] and fixed == ["今天"] and good.calls == 1,
          (echoed, fixed, good.calls))
    bad_key = echo_cache.key(f"test:{translate.PROMPT_VERSION}", "test",
                             "zh-CN", "今日は")
    echo_cache.put(bad_key, "今日は")
    good2 = EchoOrGood(echo=False)
    fixed2 = pipeline_translate(echo_cache, good2, ["今日は"])
    check("缓存里已有的原样日文也会被跳过重翻",
          fixed2 == ["今天"] and good2.calls == 1, (fixed2, good2.calls))
    echo_cache.clear()

    class EchoThenOne(translate.Translator):
        name = "echo1"
        model_label = "echo1"

        def __init__(self):
            self.one = 0

        def translate(self, texts, target, source=None, context=None):
            return list(texts)

        def translate_one(self, text, target, context=None):
            self.one += 1
            return "今天"

    retry_cache = translate.Cache(path=cfg.CACHE_DIR / "selfcheck-retry-cache.jsonl",
                                  enabled=True)
    retry_cache.clear()
    retry = EchoThenOne()
    fixed = pipeline_translate(retry_cache, retry, ["今日は"])
    check("批量原样返回时会逐句补翻",
          fixed == ["今天"] and retry.one == 1, (fixed, retry.one))
    retry_cache.clear()

    class NoSignal:
        def emit(self, *args):
            pass

    class StubEngine:
        position = 0.0

    class SkipGood(translate.Translator):
        name = "skip"
        model_label = "skip"

        def __init__(self):
            self.calls = []

        def translate(self, texts, target, source=None, context=None):
            self.calls.append(list(texts))
            return ["这是译文" for _ in texts]

    from lumina.pipeline import SourceInfo
    fake = SubtitlePipeline.__new__(SubtitlePipeline)
    fake.cues = [subtitles.Cue(0, 1, ["今日は"]),
                 subtitles.Cue(1, 2, ["やめてください"]),
                 subtitles.Cue(2, 3, ["今日は"])]
    fake.translated = ["今天", "", "今日は"]
    fake.source = SourceInfo(list(fake.cues), "ja", "ai", None)
    fake.target = "zh-CN"
    fake.config = cfg.Config(cfg.SUPPORT_DIR / "selfcheck3.toml")
    fake.cache = translate.Cache(enabled=False)
    fake.engine = StubEngine()
    fake._cond = threading.Condition()
    fake._pending_indices = []
    fake._live_session = False
    fake._transcript_verified = True
    fake._asr_lang_hint = ""
    fake.progress = NoSignal()
    fake.notice = NoSignal()
    fake.error = NoSignal()
    fake._render = lambda *args, **kwargs: None
    fake._write_marker = lambda *args, **kwargs: None
    good = SkipGood()
    fake._translator = lambda: good
    SubtitlePipeline._translate_all(fake, threading.Event())
    check("翻译只补缺失/原样返回的句子",
          len(good.calls) == 1 and good.calls[0] == ["やめてください", "今日は"],
          good.calls)
    check("补翻后全部是有效译文",
          fake.translated == ["今天", "这是译文", "这是译文"]
          and fake.untranslated_count() == 0, fake.translated)

    import re
    class Capture(translate.OpenAICompat):
        def __init__(self, context_lines=0):
            super().__init__("http://127.0.0.1:1/v1", "test", "m", context_lines=context_lines)
            self.calls = []

        def _chat(self, messages, timeout=None):
            self.calls.append(messages)
            user = messages[-1]["content"]
            count = len(re.findall(r"^\d+\. ", user, re.M))
            return "\n".join(f"译{i}" for i in range(count))

    ctx = Capture(context_lines=2)
    ctx.translate(["第一句", "第二句"], "zh-CN")
    ctx.translate(["第三句"], "zh-CN")
    prompt = ctx.calls[1][-1]["content"]
    check("上下文翻译会把前几句带给模型",
          "第一句" in prompt and "译0" in prompt, prompt[:140])
    system = ctx.calls[0][0]["content"]
    check("翻译提示词给模型语言名而不是 zh-CN 代码",
          "简体中文" in system and "zh-CN" not in system, system[:140])

    split = cfg.Config(cfg.SUPPORT_DIR / "selfcheck_split.toml")
    split.set("translate.models_split_v1", False)
    changed = translate.migrate_model_split(split)
    installed = sorted(
        [name for name in translate.list_llm_models() if translate.llm_model_ready(name)],
        key=lambda name: translate.llm_model_path(name).stat().st_size)
    expected_live = (translate._live_choice(installed) if installed
                     else "qwen2.5-0.5b-instruct-q4_k_m.gguf")
    expected_full = installed[-1] if installed else "qwen2.5-1.5b-instruct-q4_k_m.gguf"
    check("模型分工迁移：实时取最小、整片取最大",
          changed
          and split.get("translate.live_llama_model") == expected_live
          and split.get("translate.llama_model") == expected_full,
          (changed, split.get("translate.live_llama_model"),
           split.get("translate.llama_model"), installed))
    check("实时与整片分别取各自的模型",
          translate._local_model_for(split, live=True) == expected_live
          and translate._local_model_for(split, live=False) == expected_full,
          (translate._local_model_for(split, live=True),
           translate._local_model_for(split, live=False)))
    ctx.clear_context()
    ctx.translate(["第四句"], "zh-CN")
    prompt = ctx.calls[2][-1]["content"]
    check("清空上下文后不再带旧句子",
          "第一句" not in prompt and "第四句" in prompt, prompt[:140])


def pipeline_translate(cache, translator, texts):
    """只借管线的缓存+对齐逻辑，不启 mpv/线程。"""
    from lumina.pipeline import SubtitlePipeline
    fake = SubtitlePipeline.__new__(SubtitlePipeline)
    fake.cache = cache
    fake._cancel = None
    return SubtitlePipeline._translate_texts(fake, texts, translator, "zh-CN", None)


def check_language_runtime() -> None:
    section("识别语言（自动检测 / 本片覆盖 / 实时切换）")

    class Sink:
        def __init__(self):
            self.items = []

        def emit(self, *args):
            self.items.append(args)

    class Engine:
        position = 0.0
        duration = 1000.0

        def tracks_of(self, kind):
            if kind == "audio":
                return [{"id": 1, "selected": True, "lang": "eng", "external": False}]
            return []

    class FakeWhisper:
        def __init__(self):
            self.langs = []

        def detect_language(self, media, audio_index=0, **kwargs):
            return "ja"

        def transcribe_window(self, media, t0, length, cancel, lang="auto",
                              audio_index=0, **kwargs):
            self.langs.append(lang)
            if len(self.langs) == 1:
                cfg.set_media_language_hint(media, "en")
            else:
                cancel.set()
            return []

    from lumina.pipeline import SubtitlePipeline
    media = "/tmp/selfcheck-lang-media.mkv"
    cfg.set_media_language_hint(media, "auto")
    fake = SubtitlePipeline.__new__(SubtitlePipeline)
    fake.current_media = media
    fake.media_key = media
    fake.media_source = None
    fake.media_stem = "selfcheck"
    fake.audio_index_override = None
    fake.audio_lang_override = ""
    fake.config = cfg.Config(cfg.SUPPORT_DIR / "selfcheck_lang.toml")
    fake.config.set("asr.window", 6.0)
    fake.config.set("asr.overlap", 1.0)
    fake.config.set("asr.live_lead", 0.0)
    fake.engine = Engine()
    fake.whisper = FakeWhisper()
    fake.notice = Sink()
    fake.progress = Sink()
    fake.error = Sink()
    fake.cues = []
    fake.translated = []
    fake._cond = threading.Condition()
    fake._pending_indices = []
    fake._active_translator = None
    fake._live_session = True
    fake.cloud_live = False
    fake._asr_lang_hint = ""
    fake._lang_notice_key = None
    fake._lang_log_key = None
    fake._render = lambda *args, **kwargs: None
    fake._prioritize_pending = lambda position: None

    check("自动模式用语音检测覆盖错误的音轨标记",
          fake._asr_language() == "ja", fake._asr_language())
    fake._lang_notice_key = None
    fake._lang_log_key = None
    cfg.set_media_language_hint(media, "auto")
    fake._live_loop(threading.Event(), "ja", threading.Event())
    check("实时任务中途改语言，下一段窗口就生效",
          fake.whisper.langs[:2] == ["ja", "en"], fake.whisper.langs)
    check("切语言时给出可见提示",
          any("切换" in " ".join(map(str, item)) for item in fake.notice.items),
          fake.notice.items[-3:])

    class FlakyWhisper:
        def __init__(self):
            self.calls = 0

        def detect_language(self, media, audio_index=0, **kwargs):
            return "ja"

        def transcribe_window(self, media, t0, length, cancel, lang="auto",
                              audio_index=0, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise asr.AsrError("Stream ends prematurely")
            cancel.set()
            return []

    flaky = FlakyWhisper()
    fake.whisper = flaky
    fake.cloud_live = False
    fake.engine = Engine()
    fake.cues = []
    fake.translated = []
    fake._live_session = True
    fake._live_lang_checked = False
    cfg.set_media_language_hint(media, "ja")
    fake._live_loop(threading.Event(), "ja", threading.Event())
    check("实时读取中断会从当前位置重试而不是结束任务",
          flaky.calls >= 2 and not fake.error.items
          and any("重试" in " ".join(map(str, item)) for item in fake.notice.items),
          (flaky.calls, fake.error.items[-2:], fake.notice.items[-4:]))

    import json
    path = cfg.LANGUAGE_HINTS_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop(media, None)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except (OSError, ValueError):
        pass


def check_engine(media: Path) -> MpvController:
    section("mpv 引擎（无头）")
    config = cfg.Config(cfg.SUPPORT_DIR / "selfcheck3.toml")
    config.set("player.embed_mpv", False)
    controller = MpvController(config)
    launch_errors: list[str] = []
    controller.launch_failed.connect(launch_errors.append)
    controller.mpv_log.connect(lambda line: launch_errors.append(line[:160])
                               if "rror" in line or "nknown" in line else None)
    controller.launch(extra=("--vo=gpu", "--ao=null", "--force-window=no"))
    # launch 现在是异步的（不阻塞 GUI 线程），要等它真的把 mpv 拉起并连上 socket
    deadline = time.time() + 12
    while time.time() < deadline and not controller._connected.is_set():
        time.sleep(0.1)
    check("mpv 进程活着", controller.is_alive(), "; ".join(launch_errors[-3:]) or f"binary={controller.binary}")
    check("IPC 连上了", controller._connected.is_set(), "; ".join(launch_errors[-3:]))
    if not controller.is_alive():
        code = controller.proc.poll() if controller.proc else None
        tail = ""
        try:
            tail = " | ".join(Path(cfg.LOG_PATH).read_text(errors="replace")
                              .splitlines()[-4:])
        except OSError:
            pass
        print(f"      诊断：returncode={code} args={controller.proc.args[-3:] if controller.proc else None}")
        print(f"      mpv 日志尾部：{tail[:300]}")
        return controller
    if not controller.is_alive():
        return controller
    controller.open_media(str(media))
    deadline = time.time() + 12
    tracks: list[dict] = []
    while time.time() < deadline:
        tracks = controller.tracks()
        if tracks:
            break
        time.sleep(0.15)
    audio = [t for t in tracks if t.get("type") == "audio"]
    subs = [t for t in tracks if t.get("type") == "sub"]
    check("发现 2 条音轨", len(audio) == 2, len(audio))
    check("发现 2 条字幕轨", len(subs) == 2, len(subs))
    check("音轨语言标签可读回", {langtags.base_of(t.get("lang")) for t in audio} == {"en", "zh"},
          [t.get("lang") for t in audio])
    check("字幕语言标签可读回", {langtags.base_of(t.get("lang")) for t in subs} == {"en", "zh"},
          [t.get("lang") for t in subs])

    controller.set_lang_prefs(["en"], ["eng"])
    time.sleep(0.4)
    check("alang=eng 时自动选中英文轨",
          langtags.base_of(controller.get("aid") and
                           next((t.get("lang") for t in controller.tracks_of("audio")
                                 if t.get("selected")), "") or "") == "en",
          controller.get("aid"))

    duration = controller.get("duration")
    check("时长被读出来", isinstance(duration, (int, float)) and 6 < duration < 12, duration)
    # mpv 的 `idle` 是选项（一直是 true），运行时状态叫 `idle-active`；用错会让
    # 播放中按空格误判成「没放东西」而弹出模态文件框。
    try:
        idle_active = controller.call(["get_property", "idle-active"])
    except MpvError as exc:                       # noqa: F841
        idle_active = f"error: {exc}"
    check("播放中 idle-active 为 False", idle_active is False, repr(idle_active))
    check("观测列表里含 idle-active 初值", "idle-active" in controller._props,
          sorted(controller._props)[:6])

    controller.set_hwdec("no")
    time.sleep(0.6)
    actual_soft = controller.hwdec_actual()
    check("切软解后 mpv 自报 no", actual_soft in ("no", "", None), actual_soft)
    controller.set_hwdec("videotoolbox")
    time.sleep(1.0)
    actual = controller.hwdec_actual()
    # 无头环境里不一定建得出 VO（cocoa GL 构建在 --force-window=no 下不创建 surface），
    # 所以这里只断言「选项被接受」；真正的硬解证据在 verify_render 里用真实窗口验。
    accepted = controller.get("hwdec")
    accepted_name = accepted[0] if isinstance(accepted, list) and accepted else accepted
    check("切硬解后 mpv 接受了 videotoolbox", str(accepted_name) == "videotoolbox", accepted)
    if actual:
        check("有 VO 时确实报 videotoolbox", "videotoolbox" in str(actual), actual)
    else:
        print("      （无头环境没建 VO，硬解实况见 verify_render）")

    controller.set_volume(63)
    controller.set_speed(1.25)
    target = controller.get("aid")
    other = next((t.get("id") for t in controller.tracks_of("audio") if t.get("id") != target), None)
    controller.select_track("audio", other)
    time.sleep(0.3)
    check("切音轨落到另一条", controller.get("aid") == other, (target, other, controller.get("aid")))
    check("音量设置生效", abs(float(controller.get("volume")) - 63) < 1.0, controller.get("volume"))
    check("倍速设置生效", abs(float(controller.get("speed")) - 1.25) < 0.01, controller.get("speed"))

    controller.seek(3.0, absolute=True)
    time.sleep(0.8)
    controller.poll_position()      # 无头环境没有 Qt 事件循环，轮询得手动推一次
    position = controller.position
    check("绝对定位到 3 秒附近", position is not None and abs(position - 3.0) < 1.2, position)

    generated = FIXTURE_DIR / "engine-added.ass"
    subtitles.write_ass(generated, [subtitles.Cue(0.5, 2.5, ["测试译文 line1", "line2"])],
                        subtitles.AssStyle())
    before = len(controller.tracks_of("sub"))
    pid = controller.add_subtitle(str(generated), select=True)
    time.sleep(0.6)
    after = len(controller.tracks_of("sub"))
    check("sub-add 增加一条字幕轨", after == before + 1, (before, after))
    check("新轨被选中", controller.get("sid") == pid, (pid, controller.get("sid")))
    controller.reload_subtitle()
    controller.remove_subtitle(pid)
    time.sleep(0.4)
    check("sub-remove 能收回这条轨",
          len(controller.tracks_of("sub")) == before, len(controller.tracks_of("sub")))

    controller.set("sid", "no")
    time.sleep(0.2)
    check("sid=no 关闭字幕", controller.get("sid") in ("no", False, None), controller.get("sid"))

    # 内嵌不可用时的替代路径：把 mpv 的播放窗贴到我们指定的几何
    controller.pin_to_rect(120, 90, 820, 520)
    time.sleep(0.5)
    geo = controller.get("geometry")
    check("能把播放窗贴到指定几何", "820x520" in str(geo), geo)
    return controller


def check_pipeline(media: Path, controller: MpvController) -> None:
    section("字幕管线（抽取 + 双语生成）")
    config = cfg.Config(cfg.SUPPORT_DIR / "selfcheck3.toml")
    pipeline_subtitles = cfg.WORK_DIR / "selfcheck_pipeline_subtitles"
    config.set("subtitle.output_dir", str(pipeline_subtitles))
    shutil.rmtree(pipeline_subtitles, ignore_errors=True)
    pipeline = SubtitlePipeline(controller, config)
    pipeline.begin_media(str(media))
    subs = controller.tracks_of("sub")
    zh = next((t for t in subs if langtags.base_of(t.get("lang")) == "zh"), None)
    check("能找到中文字幕轨", zh is not None, subs)
    if zh is None:
        return
    source = pipeline.resolve_source(zh)
    check("内嵌字幕抽取成功", len(source.cues) == 4, len(source.cues))
    check("抽取文本是中文", any("港口的灯" in c.plain() for c in source.cues),
          [c.plain() for c in source.cues])
    check("来源标记为 embedded", source.origin == "embedded", source.origin)

    pipeline._adopt_cues(source.cues)
    pipeline.target = "zh-CN"
    stub = StubTranslator()
    cancel = threading.Event()

    def fake_build(_config, live=False):
        return stub

    import lumina.translate as tr
    original_build = tr.build
    tr.build = fake_build
    try:
        pipeline._translate_all(cancel)
    finally:
        tr.build = original_build
    check("翻译条数与原文一致", sum(1 for t in pipeline.translated if t) == len(source.cues),
          pipeline.translated)
    check("双语模式挂上了生成轨", pipeline.ai_pid is not None, pipeline.ai_pid)
    out = pipeline.output_path.read_text(encoding="utf-8")
    dialogue_count = sum(1 for ln in out.splitlines() if ln.startswith("Dialogue:"))
    check("生成文件里 4 句 × 2 行", dialogue_count == 8, dialogue_count)
    check("生成轨是当前 sid",
          pipeline.ai_pid is not None and controller.get("sid") == pipeline.ai_pid,
          (controller.get("sid"), pipeline.ai_pid))
    selected = next((t for t in controller.tracks_of("sub") if t.get("selected")), {})
    check("mpv 侧也认这条轨", selected.get("id") == pipeline.ai_pid, selected)
    native = next((t for t in controller.tracks_of("sub")
                   if t.get("id") != pipeline.ai_pid and not t.get("external")), None)
    if native is not None:
        errors: list[str] = []
        pipeline.error.connect(errors.append)
        controller.set("sid", native.get("id"))
        pipeline._render(force=True)
        check("刷新生成字幕不抢手选的原生轨",
              controller.get("sid") == native.get("id"),
              f"{controller.get('sid')} vs {native.get('id')}")
        check("手选原生轨时刷新不报 sub-reload 错", not errors, errors)
        controller.set("sid", pipeline.ai_pid)
        pipeline._render(force=True)
        check("选中生成轨时刷新字幕文件不报错", not errors, errors)

    pipeline.mode = "translated"
    pipeline._render(force=True)
    single = pipeline.output_path.read_text(encoding="utf-8")
    check("纯译文模式只剩 Trans 行",
          all(",Trans," in ln for ln in single.splitlines() if ln.startswith("Dialogue:")))
    pipeline.stop_all()
    check("stop_all 移除了生成轨", pipeline.ai_pid is None)

    pipeline.begin_media(str(media))
    restored = pipeline.restore_existing()
    check("重新打开片子会自动挂回已生成字幕",
          restored and pipeline.ai_pid is not None
          and controller.get("sid") == pipeline.ai_pid,
          f"restored={restored} pid={pipeline.ai_pid} sid={controller.get('sid')}")
    check("恢复的字幕句数对得上", len(pipeline.cues) == 4, len(pipeline.cues))
    check("恢复的译文也读回来了", any(pipeline.translated), pipeline.translated[:2])
    marker = pipeline._marker_path()
    if marker is not None:
        marker.unlink(missing_ok=True)
    pipeline._transcript_verified = False      # 模拟重启后只剩旧版文件
    check("旧版产物没有完成标记，不被当成完整转写",
          not pipeline.transcript_complete())
    pipeline._write_marker(True)
    check("写完成标记后一键翻译可以复用", pipeline.transcript_complete())
    if marker is not None and marker.exists():
        import json
        data = json.loads(marker.read_text(encoding="utf-8"))
        data.pop("asr_version", None)
        marker.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        pipeline._transcript_verified = False
        check("旧版转写标记会触发重新转写", not pipeline.transcript_complete())
    if marker is not None:
        marker.unlink(missing_ok=True)
    pipeline._transcript_verified = False      # 模拟重启后只剩半截字幕
    check("删掉标记后重新要求转写", not pipeline.transcript_complete())
    tagged = pipeline.audio_language()
    detected = pipeline.whisper.detect_language(pipeline.current_media,
                                                 pipeline._audio_ordinal())
    chosen = pipeline._asr_language()
    check("自动识别使用音轨标记或语音检测结果",
          chosen in {tagged, detected, "auto"}, (tagged, detected, chosen))
    before = len(pipeline.cues)
    pipeline._append_cue(subtitles.Cue(100.0, 101.0, ["重复测试句"]))
    pipeline._append_cue(subtitles.Cue(100.4, 101.4, ["重复测试句"]))
    check("时间重叠的重复句只保留一条", len(pipeline.cues) == before + 1,
          len(pipeline.cues) - before)
    fake_live = SubtitlePipeline.__new__(SubtitlePipeline)
    fake_live.cues = [subtitles.Cue(10.0, 12.0, ["何するの"])]
    fake_live.translated = ["要做什么"]
    fake_live._cond = threading.Condition()
    fake_live._pending_indices = []
    check("重叠窗口里文本略有差异的同一句会合并",
          not fake_live._append_cue(subtitles.Cue(11.5, 13.0, ["何するの?"]))
          and len(fake_live.cues) == 1 and fake_live.cues[0].end == 13.0,
          [(c.start, c.end, c.plain()) for c in fake_live.cues])
    check("合并时较长的文本会替换旧句并重排队翻译",
          not fake_live._append_cue(subtitles.Cue(12.0, 14.0, ["何するの? これ"]))
          and fake_live.cues[0].plain() == "何するの? これ"
          and 0 in fake_live._pending_indices,
          [(c.start, c.end, c.plain()) for c in fake_live.cues])
    clamped = SubtitlePipeline._clamp_cues(
        [subtitles.Cue(-0.5, 2.5, ["第一句"]),
         subtitles.Cue(19.9, 25.0, ["第二句"])], 0.0, 20.0)
    check("实时字幕被夹在音频窗口内",
          len(clamped) == 2 and clamped[0].start == 0.0
          and clamped[0].end == 2.5
          and clamped[1].end <= 19.98 + 1e-6,
          [(c.start, c.end) for c in clamped])
    from lumina.pipeline import SourceInfo
    saved_source = pipeline.source
    pipeline.source = SourceInfo(list(pipeline.cues or [subtitles.Cue(0, 1, ["x"])]),
                                 "en", "ai", pipeline.output_path)
    check("指定日语时不会拿现成的英语转写直接翻译",
          not pipeline.transcript_matches_language("ja"))
    check("语言一致或 auto 时允许复用",
          pipeline.transcript_matches_language("en")
          and pipeline.transcript_matches_language("EN")
          and pipeline.transcript_matches_language("auto"))
    pipeline.source = saved_source
    pipeline.stop_all()

    from lumina.media import MediaSource
    remote = MediaSource(
        uri="http://127.0.0.1:15244/dav/115/test.mp4",
        display_name="test.mp4", key="115:/115/test.mp4", origin="115",
        headers={"Authorization": "Bearer test-token"}, user_agent="test-UA")
    pipeline.begin_media(remote)
    check("云端媒体用 115 稳定 key，而不是临时 URL",
          pipeline.media_key == "115:/115/test.mp4", pipeline.media_key)
    check("云端字幕落在 app 数据的 115 目录",
          "115" in pipeline.sub_folder.parts
          and pipeline.sub_folder.name.startswith("test-"), pipeline.sub_folder)
    check("云端 headers 与 UA 分开传给 mpv/ffmpeg",
          remote.headers.get("Authorization") == "Bearer test-token"
          and remote.user_agent == "test-UA"
          and "Authorization" in remote.ffmpeg_header_text(),
          (remote.headers, remote.user_agent, remote.ffmpeg_header_text()))
    check("mpv 回报的单斜杠 URL 仍认得出是同一部片",
          remote.matches("http:/127.0.0.1:15244/dav/115/test.mp4")
          and not remote.matches("http:/127.0.0.1:15244/dav/115/other.mp4"))
    relative = MediaSource.local("multi.mkv")
    check("相对路径和 mpv 回报的绝对路径也能对上",
          relative.matches(str(Path("multi.mkv").resolve())))
    refreshed = {"count": 0}

    def refresh_headers():
        refreshed["count"] += 1
        return {"Authorization": f"Bearer new-{refreshed['count']}"}

    remote.refresh = refresh_headers
    check("刷新云端 header 会原地更新，ASR 与重开流共用同一份",
          pipeline._refresh_media_headers()
          and remote.headers.get("Authorization") == "Bearer new-1",
          remote.headers)
    from lumina.pipeline import _is_auth_error
    check("401/403 会被认成鉴权失败，partial file 不会",
          _is_auth_error(RuntimeError("Server returned 401 Unauthorized (authorization failed)"))
          and _is_auth_error(RuntimeError("403 Forbidden"))
          and not _is_auth_error(RuntimeError("stream 0, offset 0xcc1fd: partial file")))
    pipeline.stop_all()
    shutil.rmtree(pipeline.sub_folder, ignore_errors=True)


def check_download_resume() -> None:
    section("模型下载断点续传")
    import http.server

    payload = bytes(range(256)) * 4096          # 1 MiB
    state = {"requests": 0, "ranges": []}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            state["requests"] += 1
            rng = self.headers.get("Range")
            if rng:
                state["ranges"].append(rng)
            start = 0
            if rng and rng.startswith("bytes="):
                try:
                    start = int(rng.split("=")[1].split("-")[0])
                except ValueError:
                    start = 0
            body = payload[start:]
            status = 206 if start else 200
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            if status == 206:
                self.send_header("Content-Range",
                                 f"bytes {start}-{len(payload) - 1}/{len(payload)}")
            self.end_headers()
            try:
                if state["requests"] == 1:
                    # 第一次故意只发一半就断开，逼客户端保留 .part 并用 Range 续传
                    self.wfile.write(body[:len(body) // 2])
                    self.wfile.flush()
                    self.close_connection = True
                    return
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    dest = cfg.WORK_DIR / "download_resume_test.bin"
    part = dest.with_name(dest.name + ".part")
    dest.unlink(missing_ok=True)
    part.unlink(missing_ok=True)
    progresses: list[tuple[int, int]] = []
    try:
        translate.download_from_mirrors(
            [f"http://127.0.0.1:{port}/model.bin"], dest,
            on_progress=lambda done, total: progresses.append((done, total)),
            retries=3, chunk=1 << 14)
        check("断线后能从 .part 续传并完成", dest.is_file() and dest.read_bytes() == payload,
              dest.stat().st_size if dest.exists() else "缺失")
        check("续传确实发了 Range 请求",
              any(r.startswith("bytes=") for r in state["ranges"]), state["ranges"])
        check("续传后的进度总量仍是完整文件大小",
              bool(progresses) and progresses[-1][1] == len(payload), progresses[-1:])
    finally:
        server.shutdown()
        server.server_close()
        dest.unlink(missing_ok=True)
        part.unlink(missing_ok=True)


def check_formats(controller: MpvController) -> None:
    section("格式兼容（WMV / 常见音频）")
    wmv = FIXTURE_DIR / "probe.wmv"
    if not wmv.exists() or wmv.stat().st_size < 10_000:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "testsrc2=s=320x180:r=15:d=2",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
             "-c:v", "wmv2", "-c:a", "wmav2", "-shortest", str(wmv)],
            check=True, capture_output=True)

    def open_and_tracks(path: Path, predicate) -> list[dict]:
        controller.open_media(str(path))
        deadline = time.time() + 8
        seen: list[dict] = []
        while time.time() < deadline:
            if str(controller.get("path") or "") == str(path):
                seen = controller.tracks()
                if predicate(seen):
                    return seen
            time.sleep(0.1)
        return seen

    tracks = open_and_tracks(wmv, lambda ts: any(t.get("codec") == "wmv2" for t in ts))
    kinds = [t.get("type") for t in tracks]
    codecs = [t.get("codec") for t in tracks]
    check("WMV 能被打开（音视频轨都在）", "video" in kinds and "audio" in kinds, tracks)
    check("WMV 音视频解码器", "wmv2" in codecs and "wmav2" in codecs, codecs)
    deadline = time.time() + 3
    duration = None
    while time.time() < deadline:
        duration = controller.get("duration")
        if isinstance(duration, (int, float)) and duration > 0:
            break
        time.sleep(0.1)
    check("WMV 时长可读", isinstance(duration, (int, float)) and duration > 1, duration)

    aiff = FIXTURE_DIR / "en.aiff"
    if aiff.exists():
        audio_tracks = open_and_tracks(
            aiff, lambda ts: any(t.get("type") == "audio" for t in ts)
            and not any(t.get("type") == "video" for t in ts))
        check("音频文件（AIFF）能被打开", any(t.get("type") == "audio" for t in audio_tracks),
              audio_tracks)


def check_asr_helpers() -> None:
    section("ASR 进度/分段/噪声过滤")
    pct = asr._progress_ratio("whisper_print_progress_callback: progress =  62%")
    check("新版 whisper 的百分比进度能解析", pct is not None and abs(pct - 0.62) < 1e-6, pct)
    old = asr._progress_ratio("progress = 00:00:27.900 / 00:00:45.000")
    check("老版 whisper 的时间比进度也能解析", old is not None and abs(old - 0.62) < 1e-6, old)
    check("音乐/空白标记会被过滤",
          asr._is_noise("(音楽)") and asr._is_noise("[BLANK_AUDIO]")
          and asr._is_noise("♪♪") and not asr._is_noise("音乐很重要"),
          [asr._is_noise(t) for t in ("(音楽)", "[BLANK_AUDIO]", "♪♪", "音乐很重要")])
    windows = asr.Whisper.chunk_windows(1300)
    check("长片分段从 0 开始、首尾接上全片",
          bool(windows) and windows[0][0] == 0.0
          and abs(windows[-1][0] + windows[-1][1] - 1300) < 0.01,
          windows)
    covered = set()
    for start, length in windows:
        covered.update(range(int(start), min(1300, int(start + length)) + 1))
    check("分段之间没有空洞", len(covered) == 1301, len(covered))
    check("相邻分段有重叠，接住被切开的话",
          all(windows[i][0] < windows[i - 1][0] + windows[i - 1][1]
              for i in range(1, len(windows))),
          windows)
    points = asr.Whisper.language_probe_points(7325.251)
    check("长片语种检测先抽片头 5/10 分钟，不再只看 10%/50%/90%",
          bool(points) and points[0] == 300 and 600 in points and len(points) > 3,
          points[:6])
    short = asr.Whisper.language_probe_points(600)
    check("短片语种采样点都落在片长内",
          bool(short) and all(0 <= t0 < 600 for t0 in short), short)


def check_asr(media: Path) -> None:
    section("ASR（whisper.cpp / Metal）")
    config = cfg.Config(cfg.SUPPORT_DIR / "selfcheck3.toml")
    engine = asr.Whisper(config)
    ok, message = engine.availability()
    check("whisper 可用", ok, message)
    if not ok:
        return
    cancel = threading.Event()
    started = time.time()
    cues = engine.transcribe_window(str(media), 0.0, 8.0, cancel, lang="en")
    elapsed = time.time() - started
    check("窗口转写产出句子", len(cues) >= 1, [c.plain() for c in cues])
    check("时间戳落在窗口内", all(0.0 <= c.start <= 9.0 for c in cues),
          [(c.start, c.end) for c in cues])
    check("8 秒音频的转写快于实时", elapsed < 8.0, f"{elapsed:.1f}s")
    text = " ".join(c.plain().lower() for c in cues)
    check("转写内容对得上关键词", any(word in text for word in ("harbor", "ferry", "fog", "morning")), text)


def check_stream_proxy() -> None:
    section("云端单流 Range 代理")
    import http.server
    import threading
    import httpx
    from lumina.media import MediaSource
    from lumina.stream_proxy import RangeCacheProxy

    base = bytes(range(251))
    size = 20 * 1024 * 1024 + 123
    payload = (base * (size // len(base) + 1))[:size]

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            rng = self.headers.get("Range")
            start, end = 0, size - 1
            if rng and rng.startswith("bytes="):
                left, _, right = rng.split("=", 1)[1].partition("-")
                start = int(left) if left else 0
                end = min(size - 1, int(right)) if right else size - 1
            body = payload[start:end + 1]
            self.send_response(206 if rng else 200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Accept-Ranges", "bytes")
            if rng:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    source = MediaSource(uri=f"http://127.0.0.1:{server.server_address[1]}/f.bin",
                         display_name="f.bin", key="115:/f.bin", origin="115")
    proxy = RangeCacheProxy(source, cache_mb=4, pin_head_mb=1, pin_tail_mb=1)
    try:
        url = proxy.start()
        check("代理读到总大小", proxy.total() == size, proxy.total())
        with httpx.Client(timeout=20, trust_env=False) as client:
            r = client.get(url, headers={"Range": "bytes=0-1023"})
            check("代理开头 Range 正确",
                  r.status_code == 206 and r.content == payload[:1024], r.status_code)
            start = 2 * 1024 * 1024 + 17
            end = start + 1024 * 1024 + 500
            r = client.get(url, headers={"Range": f"bytes={start}-{end}"})
            check("代理跨块 Range 正确",
                  r.status_code == 206 and r.content == payload[start:end + 1],
                  (r.status_code, len(r.content)))
            tail = size - 50000
            r = client.get(url, headers={"Range": f"bytes={tail}-{size - 1}"})
            check("代理片尾预读命中",
                  r.status_code == 206 and r.content == payload[tail:size], r.status_code)

            result = {}
            def slow() -> None:
                s0 = 3 * 1024 * 1024
                e0 = s0 + 1024 * 1024
                with httpx.Client(timeout=20, trust_env=False) as c2:
                    with c2.stream("GET", url,
                                   headers={"Range": f"bytes={s0}-{e0 - 1}"}) as rr:
                        buf = bytearray()
                        for chunk in rr.iter_bytes(65536):
                            buf.extend(chunk)
                            time.sleep(0.002)
                        result["body"] = bytes(buf)
                        result["range"] = (s0, e0)
            t = threading.Thread(target=slow)
            t.start()
            time.sleep(0.1)
            s0 = 4 * 1024 * 1024
            e0 = s0 + 512 * 1024
            r = client.get(url, headers={"Range": f"bytes={s0}-{e0 - 1}"})
            check("慢读同时快读另一段",
                  r.status_code == 206 and r.content == payload[s0:e0], r.status_code)
            t.join(timeout=10)
            rs, re_ = result.get("range", (0, 0))
            check("慢读者内容完整", result.get("body") == payload[rs:re_],
                  len(result.get("body", b"")))
            s0 = 1024 * 1024
            e0 = s0 + 4096
            r = client.get(url, headers={"Range": f"bytes={s0}-{e0 - 1}"})
            check("代理回跳 Range 正确",
                  r.status_code == 206 and r.content == payload[s0:e0],
                  (r.status_code, proxy.stats()))
            # 慢读者钉在旧位置，同时从远处读：旧连接应被掐掉，不能来回重置
            far = {}
            stop = threading.Event()

            def pinned() -> None:
                s1 = 2 * 1024 * 1024
                e1 = s1 + 3 * 1024 * 1024
                try:
                    with httpx.Client(timeout=20, trust_env=False) as c2:
                        with c2.stream("GET", url,
                                       headers={"Range": f"bytes={s1}-{e1 - 1}"}) as rr:
                            for chunk in rr.iter_bytes(65536):
                                if stop.is_set():
                                    break
                                time.sleep(0.004)
                    far["old"] = "closed"
                except Exception as exc:
                    far["old"] = type(exc).__name__

            t = threading.Thread(target=pinned)
            t.start()
            time.sleep(0.2)
            s1 = 15 * 1024 * 1024
            e1 = s1 + 256 * 1024
            before = proxy.stats()["resets"]
            r = client.get(url, headers={"Range": f"bytes={s1}-{e1 - 1}"})
            stop.set()
            t.join(timeout=10)
            check("旧位置读者不会把远处请求拉回去",
                  r.status_code == 206 and r.content == payload[s1:e1]
                  and proxy.stats()["resets"] - before <= 2,
                  (r.status_code, len(r.content), proxy.stats()))
        check("代理没有频繁跳读", proxy.stats()["resets"] <= 5, proxy.stats())
    finally:
        proxy.stop()
        server.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asr", action="store_true", help="顺带跑 whisper 转写")
    parser.add_argument("--fixture-only", action="store_true")
    parser.add_argument("--rebuild-fixture", action="store_true")
    args = parser.parse_args()

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication([])  # noqa: F841  图标/控件需要 GUI 栈，offscreen 即可
    cfg.ensure_dirs()
    media = build_fixture(force=args.rebuild_fixture)
    check("测试素材生成", media.exists() and media.stat().st_size > 20_000, str(media))
    if args.fixture_only:
        print(f"\n素材：{media}")
        return 0

    check_config()
    check_download_resume()
    check_subtitle_paths()
    check_model_dirs()
    check_app_data()
    check_storage()
    check_cloud_lifecycle()
    check_cloud_multi_storage()
    check_i18n()
    check_live_stream_budget()
    check_langtags()
    check_subtitles()
    check_icons()
    check_doctor()
    check_ui()
    check_boot()
    check_launcher()
    check_translate()
    check_language_runtime()
    check_asr_helpers()
    check_stream_proxy()
    controller = check_engine(media)
    check_pipeline(media, controller)
    if controller.is_alive():
        check_formats(controller)
    if args.asr:
        check_asr(media)
    controller.quit()

    failed = [name for name, ok, _ in RESULTS if not ok]
    section("汇总")
    # 测试用的临时配置用完就删，别在应用数据目录里堆垃圾（那正是他让我们解决的问题）
    for name in ("selfcheck3.toml", "selfcheck_split.toml", "selfcheck_lang.toml",
                 "selfcheck_ui.toml", "selfcheck_cloud.toml"):
        (cfg.SUPPORT_DIR / name).unlink(missing_ok=True)
    print(f"共 {len(RESULTS)} 项，失败 {len(failed)} 项")
    for name in failed:
        detail = next(d for n, ok, d in RESULTS if n == name)
        print(f"  ✗ {name}: {detail}")
    print("SELFCHK PASS" if not failed else "SELFCHK FAIL")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
