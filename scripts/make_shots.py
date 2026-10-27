#!/usr/bin/env python
"""生成 README 用的界面截图 —— 只用现造的合成素材，绝不碰你的真实片库。

    .venv/bin/python scripts/make_shots.py            # 出到 assets/screenshots/
    .venv/bin/python scripts/make_shots.py --only player ai

为什么不用真片子：截图会公开。115 目录名、字幕里的片名、你机器上的路径都是个人信息，
所以这里只用 ffmpeg 现生成的 testsrc2 画面 + 自己写的示例字幕，并且
① 用独立的临时配置文件（不污染你的「最近打开」）；
② 字幕输出指到临时目录（不往你的字幕库里塞东西）；
③ 不截 115 浏览页（那一页必然列出你的网盘目录名）。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 截图里出现的网关路径要写成用户实际看到的样子（/Applications/…），
# 而不是源码直跑时回指仓库 dist 的那份
_packaged = Path("/Applications/Lumina.app/Contents/Resources")
if _packaged.is_dir():
    os.environ["LUMINA_APP_RES"] = str(_packaged)

from PySide6.QtCore import QPoint, QRect  # noqa: E402
from PySide6.QtGui import QPixmap, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from lumina import config as cfg, i18n  # noqa: E402
from lumina.ui import theme  # noqa: E402
from lumina.ui.main_window import MainWindow  # noqa: E402

DEMO_DIR = cfg.WORK_DIR / "readme-demo"
SHOTS = ROOT / "assets" / "screenshots"
ZH_LINES = ["第一句：这是一段合成画面，不是真实影片。", "第二句：播放器会把字幕画在这里。",
            "第三句：双语模式会再补一行译文。", "第四句：样式、位置、字号都能调。"]
EN_LINES = ["Line one: this is a synthetic test pattern.",
            "Line two: the player renders subtitles here.",
            "Line three: bilingual mode adds a translated line.",
            "Line four: style, position and size are adjustable."]
PAGES = {"player": None, "ai": "ai", "tracks": "tracks", "settings": "settings",
         "storage": "storage", "service": "cloud_manage", "playlist": "playlist"}


def write_srt(path: Path, lines: list[str]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for index, text in enumerate(lines):
            start = index * 3
            handle.write(f"{index + 1}\n")
            handle.write(f"00:00:{start:02d},000 --> 00:00:{start + 3:02d},000\n")
            handle.write(f"{text}\n\n")


def build_demo() -> Path:
    """12 秒 1280x720 合成画面 + 两条音轨 + 中英两条字幕轨。"""
    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    media = DEMO_DIR / "demo.mkv"
    if media.is_file() and media.stat().st_size > 100_000:
        return media
    write_srt(DEMO_DIR / "zh.srt", ZH_LINES)
    write_srt(DEMO_DIR / "en.srt", EN_LINES)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=12",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=12",
                    "-f", "lavfi", "-i", "sine=frequency=660:duration=12",
                    "-map", "0:v", "-map", "1:a", "-map", "2:a",
                    "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=chi",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
                    "-c:a", "aac", "-ac", "1", str(DEMO_DIR / "base.mkv")], check=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(DEMO_DIR / "base.mkv"),
                    "-i", str(DEMO_DIR / "en.srt"), "-i", str(DEMO_DIR / "zh.srt"),
                    "-map", "0", "-map", "1", "-map", "2",
                    "-c", "copy", "-c:s", "srt",
                    "-metadata:s:s:0", "language=eng", "-metadata:s:s:1", "language=chi",
                    str(media)], check=True)
    return media


def settle(app: QApplication, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def shot(window: MainWindow):
    """抓整窗，再把 GL 画面贴回视频区。

    QWidget.grab() 抓 QOpenGLWidget 只会得到一片黑（真实踩过），
    所以视频区单独用 grabFramebuffer() 取，再按坐标合成。
    """
    chrome = window.grab()
    host = getattr(window, "video_host", None)
    if host is None or not hasattr(host, "grabFramebuffer") or not host.isVisible():
        return chrome
    try:
        frame = host.grabFramebuffer()
    except Exception:
        return chrome
    if frame.isNull():
        return chrome
    merged = chrome.copy()
    painter = QPainter(merged)
    origin = host.mapTo(window, QPoint(0, 0))
    # grabFramebuffer 给的是 QImage，且 retina 下物理像素是逻辑尺寸的 2 倍：
    # 转成 QPixmap 并把 devicePixelRatio 带上，画进逻辑矩形才不会糊成两倍大
    pixmap = QPixmap.fromImage(frame)
    pixmap.setDevicePixelRatio(frame.devicePixelRatio() or 1.0)
    painter.drawPixmap(QRect(origin, host.size()), pixmap)
    # 控制条/首屏卡片是独立的浮层窗口，grab(整窗) 抓不到，按全局坐标叠回去
    overlay = getattr(window, "overlay", None)
    if overlay is not None and overlay.isVisible():
        layer = overlay.grab()
        if not layer.isNull():
            spot = overlay.mapToGlobal(QPoint(0, 0)) - window.mapToGlobal(QPoint(0, 0))
            painter.drawPixmap(QRect(spot, overlay.size()), layer)
    painter.end()
    return merged


def build_demo_en() -> Path:
    """英文截图用：同一条合成画面，只带英文一条字幕轨。

    不去赌「语言偏好」的选轨逻辑 —— 截图要的是确定的画面。
    base.mkv 可能是旧版本留下的、自带字幕轨的缓存，所以这里只显式映射画面与音轨。
    """
    base = build_demo()
    target = DEMO_DIR / "demo-en.mkv"
    if not (target.is_file() and target.stat().st_size > 100_000):
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-i", str(base), "-i", str(DEMO_DIR / "en.srt"),
                        "-map", "0:v", "-map", "0:a", "-map", "1",
                        "-c", "copy", "-c:s", "srt",
                        "-metadata:s:s:0", "language=eng", str(target)], check=True)
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", default=list(PAGES))
    parser.add_argument("--lang", choices=("zh", "en"), default="zh",
                        help="界面语言；en 出到 assets/screenshots/en/")
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    want = [name for name in PAGES if not args.only or name in args.only]

    media = build_demo_en() if args.lang == "en" else build_demo()
    out = Path(args.out) if args.out else (SHOTS if args.lang == "zh" else SHOTS / "en")
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    # 独立配置 + 独立字幕目录：截图跑完不在你的数据目录里留下任何痕迹
    temp_config = cfg.SUPPORT_DIR / "make_shots.toml"
    config = cfg.Config(temp_config)
    config.set("ui.language", args.lang)
    i18n.set_language(i18n.resolve(args.lang))
    if args.lang == "en":
        # 英文截图里字幕/音轨也用英文轨（演示片本身两条音轨、一条英文字幕轨）
        config.set("languages.default_subtitle_langs", ["en"])
        config.set("languages.default_audio_langs", ["en"])
    config.set("player.backend", "libmpv")
    config.set("subtitle.output_dir", str(DEMO_DIR / "subs"))
    config.set("subtitle.next_to_video", False)
    config.set("cloud.start_mode", "auto")     # 服务页要拍到"运行中"，网关由软件自己带
    app.setStyleSheet(theme.build_qss(config.get("ui.accent", "#4fa8ff")))

    window = MainWindow(config)
    window.resize(1180, 700)
    window.show()
    settle(app, 2.0)
    window.open_path(str(media))
    deadline = time.time() + 15
    while time.time() < deadline and not window.engine.tracks():
        settle(app, 0.2)
    settle(app, 1.5)
    window.engine.set("pause", True)          # 画面停在有字幕的一帧
    subs = window.engine.tracks_of("sub")
    zh = next((t for t in subs if (t.get("lang") or "").startswith(("chi", "zho"))), None)
    if zh is not None:
        window.engine.select_track("sub", zh.get("id"))
    window.engine.seek(1.0, absolute=True)
    settle(app, 1.2)

    shots = []
    for name in want:
        page = PAGES[name]
        if page is None:
            window.hide_sidebar()
        else:
            window.show_sidebar(page)
        window.overlay.bar.setVisible(True)
        settle(app, 1.0)
        if name == "storage":
            # 体积是后台一行一行算的，整个数据目录要十几秒：等它算完再拍
            deadline = time.time() + 40
            while time.time() < deadline and "应用数据目录共" not in \
                    window.sidebar.storage.total_label.text():
                settle(app, 0.5)
        elif name == "service":
            settle(app, 2.5)      # 状态与 115 存储配置都是异步读的
        target = out / f"{name}.png"
        shot(window).save(str(target))
        shots.append(str(target))
        print(f"  ✓ {target}")
    window.close()
    settle(app, 1.0)
    temp_config.unlink(missing_ok=True)
    print("SELFSHOTS " + " ".join(shots))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
