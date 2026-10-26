#!/usr/bin/env python
"""真内嵌验证：画面是不是真的画进了我们自己的 GL 控件，以及画得对不对。

和 verify_render.py（ipc + 贴合窗口）互斥使用：这条路线不再有 mpv 子进程，
所以判据换成
  · widget.grab() 自证出图（不需要录屏权限），用「上红下蓝」素材直接量朝向；
  · 帧率、hwdec-current、AO/VO 日志由 libmpv 自己报；
  · 顶层窗口只有一个，且系统里没有 mpv 进程。
跑法：uv run python scripts/verify_embed.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QImage, QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from lumina import config as cfg  # noqa: E402
from lumina.ui.main_window import MainWindow  # noqa: E402

MEDIA = cfg.WORK_DIR / "fixture" / "multi.mkv"
HALVES = cfg.WORK_DIR / "fixture" / "halves.mkv"
FAILURES: list[str] = []


def expect(name: str, condition, detail: str = "") -> bool:
    ok = bool(condition)
    print(f"[{'OK  ' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)
    return ok


def settle(app: QApplication, seconds: float = 0.3) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def build_halves() -> None:
    """上一半红、下一半蓝：朝向这种「看图才知道」的事，用可量化的色块来判。"""
    HALVES.parent.mkdir(parents=True, exist_ok=True)
    if HALVES.exists():
        return
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "color=c=red:s=640x360:r=25:d=6",
         "-f", "lavfi", "-i", "color=c=blue:s=640x360:r=25:d=6",
         "-filter_complex", "[0][1]vstack",
         "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p", str(HALVES)],
        check=True, capture_output=True)


def shot_of(widget) -> QImage:
    """QOpenGLWidget 的内容只能走 grabFramebuffer()：grab() 会去画 Qt 侧的后备存储，
    量到的是一片黑（这个坑踩过一次，别把它当成「画面没出来」）。"""
    return widget.grabFramebuffer()


def sample(image: QImage, rows: list[float]) -> list[tuple[int, int, int]]:
    """按 rows（0=顶，1=底）在控件水平中线取几个像素，返回 RGB。"""
    image = image.convertToFormat(QImage.Format_RGB888)
    if image.isNull():
        return []
    out = []
    for frac in rows:
        y = min(image.height() - 1, max(0, int(image.height() * frac)))
        x = image.width() // 2
        px = image.pixel(x, y)
        out.append(((px >> 16) & 255, (px >> 8) & 255, px & 255))
    return out


def main() -> int:
    app = QApplication([])
    if not MEDIA.exists():
        subprocess.run([sys.executable, str(ROOT / "scripts" / "selfcheck.py"),
                        "--fixture-only"], check=True)
    build_halves()

    config = cfg.Config(cfg.SUPPORT_DIR / "verify_embed.toml")
    config.set("cloud.start_mode", "manual")   # 不碰用户的共享网关
    config.set("player.backend", "libmpv")
    config.set("player.hwdec", "auto-safe")
    config.set("player.volume", 0)        # 真的走音频输出，只是别在跑测试时出声
    # 别让自动挂回上一轮生成的字幕影响「2 音 2 字幕」的轨道判据
    subtitle_dir = cfg.WORK_DIR / "verify_embed_subtitles"
    shutil.rmtree(subtitle_dir, ignore_errors=True)
    config.set("subtitle.output_dir", str(subtitle_dir))
    window = MainWindow(config)
    expect("后端是 libmpv", window.backend == "libmpv" and
           type(window.engine).__name__ == "LibmpvPlayer", window.backend)
    # libmpv 是同步拉起来的：窗口还没显示就该已经能用，不该有「引擎启动中」那一段
    expect("构造完引擎就绪（不用等首屏）", bool(window.engine.handle),
           str(window.engine.handle))

    window.resize(1100, 700)
    window.show()
    settle(app, 1.0)
    expect("引擎起来了（handle 非空）", window.engine.is_alive() and bool(window.engine.handle))
    expect("画面控件是 QOpenGLWidget",
           type(window.video_host).__name__ == "MpvVideoWidget")
    expect("控制条成了子控件（不再是第二个窗口）", window.overlay.parent() is window.video_area,
           str(window.overlay.parent()))

    # 首屏卡片：摆正（别和左上角标题叠在一起）+ 按钮文字不被压扁
    card, bar_btn = window.welcome, window.overlay.bar.play_button
    deadline = time.time() + 10          # 体检在后台线程跑，等它真的把结果送回来
    while time.time() < deadline and not window.doctor.items:
        settle(app, 0.1)
    expect("环境体检结果能异步回到首屏", bool(window.doctor.items), len(window.doctor.items))
    settle(app, 0.4)
    expect("首屏卡片居中显示", card.x() > 0 and card.y() > 0, str(card.geometry()))
    open_btn = next(b for b in card.findChildren(QPushButton) if "打开" in b.text())
    expect("「打开视频」按钮文字放得下",
           open_btn.height() >= open_btn.minimumSizeHint().height()
           and open_btn.width() >= open_btn.minimumSizeHint().width(),
           f"{open_btn.geometry()} vs 需要 {open_btn.minimumSizeHint()}")

    # -------------------------------------------------------- 出图 + 朝向
    window.open_path(str(HALVES))
    deadline = time.time() + 12
    while time.time() < deadline and window.video_host.frames < 3:
        settle(app, 0.1)
    expect("GL 上下文建起来了", window.video_host.ctx is not None,
           "; ".join(window.video_host.errors[:3]))
    expect("控件真的在画帧", window.video_host.frames >= 3, window.video_host.frames)
    # 等到 mpv 真的解出视频轨再取色：前几帧是黑屏，量早了会误判成「没画面」
    deadline = time.time() + 12
    while time.time() < deadline and not window.engine.get("width"):
        settle(app, 0.1)
    settle(app, 0.4)

    shot = shot_of(window.video_host)
    top, middle, bottom = sample(shot, [0.12, 0.5, 0.88])
    print(f"      取色 top={top} mid={middle} bottom={bottom} "
          f"flip_y={window.video_host.flip_y}")
    expect("画面不是黑的（真的解码出图了）", sum(top) + sum(bottom) > 120, (top, bottom))
    expect("朝向正确：上红下蓝", top[0] > 120 and top[2] < 90 and bottom[2] > 120 and bottom[0] < 90,
           f"top={top} bottom={bottom}（若上下颠倒，把 MpvVideoWidget.flip_y 改成 "
           f"{1 - int(window.video_host.flip_y)}）")

    # 内嵌模式下 mpv 自己的 screenshot 命令等不到帧（实测 8s 无回复），截图必须走我们的 framebuffer
    shots = cfg.WORK_DIR / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    known = set(shots.glob("lumina-*.png"))
    window._screenshot()
    settle(app, 0.4)
    fresh = sorted(set(shots.glob("lumina-*.png")) - known)
    expect("截图命令产出文件", bool(fresh), str(fresh))
    if fresh:
        saved = QImage(str(fresh[-1]))
        rows = sample(saved, [0.12, 0.88])
        expect("截图就是屏幕上的那一帧（上红下蓝）",
               len(rows) == 2 and rows[0][0] > 120 and rows[1][2] > 120, str(rows))

    # -------------------------------------------------------- 帧率 / 硬解
    # 后台进程的 paint 事件会被 macOS 按可见性节流，靠墙钟数帧不可靠；
    # 这里用 repaint() 强制同步重绘，判据换成「渲染有没有真的推进播放」+「单帧开销」。
    pos0 = window.engine.get("time-pos") or 0.0
    frames0, hits0 = window.video_host.frames, window.video_host.update_hits
    paints: list[float] = []
    at_start = time.perf_counter()
    while time.perf_counter() - at_start < 2.0:
        window.video_host.repaint()
        paints.append(window.video_host.paint_ms)
        settle(app, 0.02)
    frames = window.video_host.frames - frames0
    hits = window.video_host.update_hits - hits0
    pos1 = window.engine.get("time-pos") or 0.0
    paints.sort()
    median_paint = paints[len(paints) // 2] if paints else 0.0
    expect("渲染真的在推进播放（2s 内走过 ≥1s）", pos1 - pos0 >= 1.0,
           f"time-pos {pos0:.2f}→{pos1:.2f}，重绘 {frames} 帧，mpv 更新回调 {hits}")
    expect("单帧渲染不吃掉主循环（中位 <12ms）", median_paint < 12,
           f"{median_paint:.1f} ms（{frames} 帧 / 2s）")
    expect("硬解仍然生效", "videotoolbox" in window.engine.hwdec_actual(),
           window.engine.hwdec_actual())

    # -------------------------------------------------------- 轨道 / 字幕
    window.open_path(str(MEDIA))
    deadline = time.time() + 15
    while time.time() < deadline and len(window.engine.tracks()) < 5:
        settle(app, 0.1)
    tracks = window.engine.tracks()
    expect("轨道齐全（2 音 2 字幕）", len(window.engine.tracks_of("audio")) == 2
           and len(window.engine.tracks_of("sub")) == 2, len(tracks))
    # 管线里的媒体必须是绝对路径且真实存在 —— 曾被 filename（裸文件名）覆盖，
    # 后果是 whisper 抽音频报 No such file or directory，语音转字幕整个功能废掉
    current = window.pipeline.current_media or ""
    expect("管线持有的是绝对且存在的媒体路径",
           bool(current) and Path(current).is_absolute() and Path(current).exists(),
           repr(current))
    # halves.mkv 没有音轨，AO 只有真放带声的片子才会初始化
    audio_out = [line for line in window.engine.log_tail if "AO:" in line]
    expect("音频输出真的起了（不是 null）",
           bool(audio_out) and "null" not in audio_out[-1],
           str(audio_out[-1:] or list(window.engine.log_tail)[-6:]))
    subs = window.engine.tracks_of("sub")
    if subs:
        window.engine.select_track("sub", subs[0].get("id"))
        settle(app, 0.4)
        expect("选字幕轨后 sid 生效",
               str(window.engine.get("sid")) == str(subs[0].get("id")),
               f"{window.engine.get('sid')} vs {subs[0].get('id')}")

    # -------------------------------------------------------- 交互仍然在
    was_paused = bool(window.engine.get("pause"))
    window.overlay.bar.play_button.click()
    settle(app, 0.4)
    expect("点播放/暂停切了状态", bool(window.engine.get("pause")) != was_paused,
           f"{was_paused} → {window.engine.get('pause')}")
    position = window.engine.get("time-pos") or 0.0
    window.engine.seek(3.0, absolute=True)
    settle(app, 0.8)
    moved = abs((window.engine.get("time-pos") or 0.0) - 3.0)
    expect("seek 后进度对上（±1s）", moved < 1.0, f"was={position} now={window.engine.get('time-pos')}")

    # --- 鼠标悬停视频区：控制条自动出现；移走后自动收起
    overlay = window.overlay
    overlay.bar.hide()
    center = QPointF(overlay.width() / 2, overlay.height() / 2)
    QApplication.sendEvent(overlay, QMouseEvent(
        QEvent.MouseMove, center, QPointF(overlay.mapToGlobal(center.toPoint())),
        Qt.NoButton, Qt.NoButton, Qt.NoModifier))
    settle(app, 0.2)
    expect("鼠标移进视频区，控制条自动出现", overlay.bar.isVisible())
    expect("控制条显示时鼠标指针不变", window.video_area.cursor().shape() != Qt.BlankCursor,
           str(window.video_area.cursor().shape()))
    overlay.leaveEvent(QEvent(QEvent.Leave))
    # 测试进程无法可靠地把系统光标挪出窗口，直接按「不悬停」执行同一条收起逻辑
    overlay._maybe_hide(ignore_pointer=True)
    settle(app, 0.1)
    expect("鼠标移出视频区，控制条自动收起", not overlay.bar.isVisible())

    # --- 控制条浮着的时候，鼠标必须还在（早先视频区无条件 BlankCursor，一移进去箭头就没了）
    window.overlay.poke()
    settle(app, 0.2)
    shape = window.video_area.cursor().shape()
    expect("控制条收起前鼠标在画面上可见", shape != Qt.BlankCursor, str(shape))
    # 控制条自动收起后也不许把光标一起藏（用户连着报过两次「鼠标在播放区消失」）
    window.overlay._autohide = True
    window.overlay._maybe_hide()
    settle(app, 0.2)
    after = window.video_area.cursor().shape()
    expect("控制条收起后鼠标仍然可见", after != Qt.BlankCursor,
           f"bar={window.overlay.bar.isVisible()} cursor={after}")

    # -------------------------------------------------------- 单窗口取证
    visible = [(type(w).__name__, w.windowTitle(), w.geometry().getRect())
               for w in QApplication.topLevelWidgets()
               if w.isVisible() and w.isWindow() and w.geometry().width() > 100]
    expect("只有一个顶层窗口", len(visible) == 1, str(visible))
    mpv_procs = subprocess.run(["pgrep", "-x", "mpv"], capture_output=True, text=True).stdout.split()
    expect("系统里没有多余的 mpv 进程", len(mpv_procs) == 0, str(mpv_procs))

    # -------------------------------------------------------- 关窗口不崩
    window.close()
    settle(app, 0.6)
    expect("关闭后渲染上下文与 handle 都释放了",
           window.video_host.ctx is None and window.engine.handle is None)
    expect("渲染过程没有 GL 报错", not window.video_host.errors,
           "; ".join(window.video_host.errors[:3]))
    config.path.unlink(missing_ok=True)      # 测试用的临时配置不留进应用数据目录

    print("\nEMBED PASS" if not FAILURES else f"\nEMBED FAIL: {FAILURES}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
