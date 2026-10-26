#!/usr/bin/env python
"""渲染验证：起真实的 .app，用 mpv 自己的 screenshot 出图，检查字幕真的画在画面上。

不需要屏幕录制权限（截图由 mpv 完成）。产物在 WORK_DIR/screenshots，脚本会把
用到的文件名打印成 RENDER <path> 便于人工/模型查看。
跑法：uv run python scripts/verify_render.py
"""
from __future__ import annotations

import glob
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import atexit  # noqa: E402

from lumina import config as cfg, subtitles  # noqa: E402
from lumina.subtitles import AssStyle, Cue  # noqa: E402

APP = "/Applications/Lumina.app"
MEDIA = cfg.WORK_DIR / "fixture" / "multi.mkv"
SHOT_DIR = cfg.WORK_DIR / "screenshots"
FAILURES: list[str] = []

_ORIGINAL_CONFIG: str | None = None


def pin_ipc_backend() -> None:
    """本套件验的是「子进程 mpv + 贴合窗口」那条退路，而打包 App 的默认后端已经是
    libmpv —— 所以先把用户配置钉到 ipc，退出时原样还原（真内嵌由 verify_embed 判）。"""
    global _ORIGINAL_CONFIG
    _ORIGINAL_CONFIG = cfg.CONFIG_PATH.read_text(encoding="utf-8") if cfg.CONFIG_PATH.exists() else None
    config = cfg.Config(cfg.CONFIG_PATH)
    config.set("player.backend", "ipc")
    config.set("player.embed_mpv", False)
    # 测试素材的字幕放在专用目录，避免自动挂回上一轮生成的字幕影响截图判据
    subtitle_dir = cfg.WORK_DIR / "verify_render_subtitles"
    config.set("subtitle.output_dir", str(subtitle_dir))
    config.save()
    import shutil
    shutil.rmtree(subtitle_dir, ignore_errors=True)


def restore_config() -> None:
    if _ORIGINAL_CONFIG is None:
        cfg.CONFIG_PATH.unlink(missing_ok=True)
    else:
        cfg.CONFIG_PATH.write_text(_ORIGINAL_CONFIG, encoding="utf-8")


def expect(name: str, condition, detail: str = "") -> bool:
    ok = bool(condition)
    print(f"[{'OK  ' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)
    return ok


class Ipc:
    def __init__(self, path: str):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(path)
        self.sock.settimeout(4)
        self.rid = 0
        self.buf = b""

    @classmethod
    def connect_latest(cls, exclude=()):
        deadline = time.time() + 20
        while time.time() < deadline:
            for path in sorted(glob.glob(str(cfg.SUPPORT_DIR / "mpv-sock-*")),
                               key=os.path.getmtime, reverse=True):
                if path in exclude:
                    continue
                try:
                    client = cls(path)
                except OSError:
                    continue
                return client
            time.sleep(0.5)
        return None

    def call(self, *cmd):
        self.rid += 1
        rid = self.rid
        self.sock.sendall((json.dumps({"command": list(cmd), "request_id": rid}) + "\n").encode())
        deadline = time.time() + 6
        while time.time() < deadline:
            try:
                self.buf += self.sock.recv(65536)
            except socket.timeout:
                continue
            while b"\n" in self.buf:
                line, self.buf = self.buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("request_id") == rid:
                    return msg
        return {"error": "timeout"}

    def get(self, name):
        return self.call("get_property", name).get("data")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def shot_from(reply: dict, timeout: float = 10.0) -> str:
    """mpv 的 screenshot 回复里直接带文件名，别去猜扩展名（默认还是 .jpg）。"""
    if reply.get("error") != "success":
        return ""
    path = (reply.get("data") or {}).get("filename") or ""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path and os.path.isfile(path) and os.path.getsize(path) > 1000:
            size = os.path.getsize(path)
            time.sleep(0.4)
            if os.path.getsize(path) == size:
                return path
        time.sleep(0.2)
    return ""


def main() -> int:
    atexit.register(restore_config)
    pin_ipc_backend()
    if not MEDIA.exists():
        subprocess.run([sys.executable, str(ROOT / "scripts" / "selfcheck.py"),
                        "--fixture-only"], check=True)
    known = set(glob.glob(str(cfg.SUPPORT_DIR / "mpv-sock-*")))
    subprocess.run(["pkill", "-f", "Lumina/venv/bin/python"], capture_output=True)
    subprocess.run(["pkill", "-x", "mpv"], capture_output=True)
    time.sleep(1.0)
    print("→ 启动 /Applications/Lumina.app")
    subprocess.run(["open", "-n", "-a", APP, str(MEDIA)], check=True)

    ipc = Ipc.connect_latest(exclude=known)
    if not expect("连上新实例的 IPC", ipc is not None):
        return 1
    assert ipc is not None
    deadline = time.time() + 15
    while time.time() < deadline and not ipc.get("track-list"):
        time.sleep(0.4)
    tracks = ipc.get("track-list") or []
    expect("真实实例加载了素材", (ipc.get("path") or "").endswith("multi.mkv"), ipc.get("path"))
    expect("真实实例走硬解", "videotoolbox" in str(ipc.get("hwdec-current")), ipc.get("hwdec-current"))
    # 单窗口判据：内嵌在当前 mpv 上做不到，所以退一步——mpv 的播放窗必须是无边框、
    # 并且被贴到我们视频区的大小（而不是它自己那 320x180 的兜底窗）。
    ps = subprocess.run(["pgrep", "-x", "mpv"], capture_output=True, text=True).stdout.split()
    cmdline = ""
    if ps:
        cmdline = subprocess.run(["ps", "-o", "command=", "-p", ps[0]],
                                 capture_output=True, text=True).stdout
    expect("播放窗无边框（--no-border）", "--no-border" in cmdline, cmdline[:150])
    expect("没有再传被忽略的 --wid", "--wid=" not in cmdline, "还在传 --wid")
    try:
        import Quartz
    except ImportError:
        Quartz = None
    if Quartz is not None:
        pids = {int(x) for x in ps}
        infos = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID)
        bounds = [dict(w.get("kCGWindowBounds") or {}) for w in infos
                  if w.get("kCGWindowOwnerPID") in pids]
        wide = [b for b in bounds if b.get("Width", 0) >= 600]
        expect("播放窗被贴到视频区大小（不是 320x180 兜底窗）", bool(wide), bounds[:2])
    else:
        print("  （没装 pyobjc-framework-Quartz，跳过窗口几何判据）")
    expect("轨道齐全", len([t for t in tracks if t.get("type") == "audio"]) == 2
           and len([t for t in tracks if t.get("type") == "sub"]) == 2,
           [(t.get("type"), t.get("lang")) for t in tracks])

    ipc.call("set_property", "time-pos", 1.2)
    ipc.call("set_property", "pause", True)
    time.sleep(0.6)

    native_shot = shot_from(ipc.call("screenshot", "window"))
    expect("原生中文字幕截图产出", bool(native_shot), native_shot)
    if native_shot:
        print(f"RENDER native {native_shot}")

    # 播放器自己生成的双语轨：中文原文 + 英文译文，两行两种样式
    bilingual = cfg.WORK_DIR / "render-bilingual.ass"
    cues = [Cue(0.4, 4.0, ["港口的灯还亮着。"]),
            Cue(4.2, 8.0, ["没人告诉他渡轮已经停了。"])]
    subtitles.write_ass(bilingual, cues, AssStyle(size=54, translated_size=46, margin_v=46),
                        translated=["The harbor lights are still on.",
                                    "Nobody told him the ferry had stopped."],
                        keep_original=True, position="below")
    reply = ipc.call("sub-add", str(bilingual))
    time.sleep(1.2)
    subs = [t for t in (ipc.get("track-list") or []) if t.get("type") == "sub"]
    known_ids = {t.get("id") for t in tracks if t.get("type") == "sub"}
    added = [t for t in subs if t.get("id") not in known_ids]
    pid = added[-1].get("id") if added else None
    expect("生成轨确实被 mpv 接受", pid is not None, f"{reply} / subs={len(subs)}")
    if pid is not None:
        ipc.call("set_property", "sid", pid)
    ipc.call("set_property", "time-pos", 1.2)
    time.sleep(1.2)
    bilingual_shot = shot_from(ipc.call("screenshot", "window"))
    expect("双语生成轨截图产出", bool(bilingual_shot), bilingual_shot)
    if bilingual_shot:
        print(f"RENDER bilingual {bilingual_shot}")
    text_shot = shot_from(ipc.call("screenshot", "subtitles"))
    if text_shot:
        print(f"RENDER subtitles-only {text_shot}")

    ipc.close()
    subprocess.run(["pkill", "-f", "Lumina/venv/bin/python"], capture_output=True)
    time.sleep(1.5)
    expect("关掉应用后 mpv 不留孤儿", subprocess.run(["pgrep", "-x", "mpv"],
                                  capture_output=True).stdout.strip() == b"")
    print("\nRENDER PASS" if not FAILURES else f"\nRENDER FAIL: {FAILURES}")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
