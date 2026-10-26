#!/usr/bin/env python
"""AI 字幕端到端验证（不需要翻译密钥）：
无字幕素材 → whisper 整片转写 → 生成 ASS → 作为字幕轨挂上 mpv → 播放头能看到。
再验一条：切到双语/译文但没配密钥时，必须是清晰的错误提示，而不是崩或静默。

跑法：uv run python scripts/verify_ai_flow.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from lumina import config as cfg, subtitles  # noqa: E402
from lumina.engine import MpvController  # noqa: E402
from lumina.pipeline import SubtitlePipeline  # noqa: E402

FIXTURE = cfg.WORK_DIR / "fixture"
NOSUB = FIXTURE / "nosub.mkv"
FAILURES: list[str] = []


def expect(name: str, condition, detail: str = "") -> bool:
    ok = bool(condition)
    print(f"[{'OK  ' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)
    return ok


def build_nosub() -> Path:
    FIXTURE.mkdir(parents=True, exist_ok=True)
    source = FIXTURE / "multi.mkv"
    if not source.exists():
        subprocess.run([sys.executable, str(ROOT / "scripts" / "selfcheck.py"),
                        "--fixture-only"], check=True)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
         "-map", "0:v", "-map", "0:a:0", "-c", "copy", str(NOSUB)], check=True)
    return NOSUB


def main() -> int:
    app = QApplication([])  # noqa: F841
    media = str(build_nosub())
    streams = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "s", "-show_entries", "stream=index",
         "-of", "csv=p=0", media], capture_output=True, text=True).stdout.strip()
    expect("素材确实没有字幕流", streams == "", streams)

    config = cfg.Config(cfg.SUPPORT_DIR / "verify_ai.toml")
    config.set("player.backend", "ipc")
    config.set("player.embed_mpv", False)
    config.set("languages.subtitle_display", "original")
    config.set("asr.language", "en")
    ai_subtitles = cfg.WORK_DIR / "verify_ai_subtitles"
    config.set("subtitle.output_dir", str(ai_subtitles))
    import shutil
    shutil.rmtree(ai_subtitles, ignore_errors=True)

    engine = MpvController(config)
    engine.launch(extra=("--vo=gpu", "--ao=null", "--force-window=no"))
    # launch 是异步的（不堵 GUI 线程），等它真起来
    deadline = time.time() + 12
    while time.time() < deadline and not engine._connected.is_set():
        time.sleep(0.1)
    expect("mpv 起来了", engine.is_alive() and engine._connected.is_set())
    pipeline = SubtitlePipeline(engine, config)
    engine.open_media(media)
    deadline = time.time() + 10
    while time.time() < deadline and not engine.tracks():
        time.sleep(0.1)
    pipeline.begin_media(media)

    notices: list[str] = []
    errors: list[str] = []
    pipeline.notice.connect(notices.append)
    pipeline.error.connect(errors.append)

    pipeline.generate_ai(live=False)
    waited = 0.0
    while waited < 90.0:
        app.processEvents()
        if len(pipeline.cues) >= 3 and pipeline.output_path and pipeline.output_path.exists():
            break
        if errors:
            break
        time.sleep(0.4)
        waited += 0.4
    expect(f"转写产出句子（{waited:.0f}s）", len(pipeline.cues) >= 3,
           [c.plain() for c in pipeline.cues][:4])
    text = " ".join(c.plain() for c in pipeline.cues).lower()
    expect("转写内容对得上素材台词",
           any(word in text for word in ("harbor", "ferry", "fog", "morning")), text[:120])
    expect("生成轨已挂到 mpv", pipeline.ai_pid is not None, str(pipeline.ai_pid))
    expect("整片转写写了完成标记，一键翻译可直接复用", pipeline.transcript_complete())
    expect("每条字幕不超 44 字", all(len(c.plain()) <= 44 for c in pipeline.cues),
           max((len(c.plain()) for c in pipeline.cues), default=0))
    time.sleep(0.6)
    app.processEvents()
    expect("生成轨是当前选中字幕", pipeline.ai_pid is not None and engine.get("sid") == pipeline.ai_pid,
           f"{engine.get('sid')} vs {pipeline.ai_pid}")
    written = pipeline.output_path.read_text(encoding="utf-8") if pipeline.output_path else ""
    dialogue = [ln for ln in written.splitlines() if ln.startswith("Dialogue:")]
    expect("生成的 ASS 有对白行", len(dialogue) >= 3, len(dialogue))
    reparsed = subtitles.parse_ass(written)
    expect("生成的 ASS 可被解析回同样句数", len(reparsed) == len(dialogue),
           f"{len(reparsed)} vs {len(dialogue)}")
    raw_path = pipeline.original_path
    raw = raw_path.read_text(encoding="utf-8") if raw_path and raw_path.exists() else ""
    expect("真实转写也落了 <视频名>.原文.srt",
           bool(raw_path) and "-->" in raw
           and raw_path.name == f"{NOSUB.stem}.原文.srt",
           str(raw_path))
    expect("三份产物都在以视频名命名的同一个文件夹里",
           pipeline.sub_folder.name == NOSUB.stem
           and pipeline.output_path.parent == pipeline.sub_folder
           and pipeline.original_path.parent == pipeline.sub_folder,
           f"{pipeline.sub_folder} / {pipeline.output_path.parent}")

    # 生成的字幕也要能单独翻译：直接用内存里的原文，不重新跑 whisper
    from lumina import translate

    class GenStub(translate.Translator):
        name = "stub"
        model_label = "stub-gen"

        def translate(self, texts, target, source=None, context=None):
            return [f"译：{t}" for t in texts]

    original_build = translate.build
    translate.build = lambda _config, live=False: GenStub()
    try:
        errors.clear()
        pipeline.mode = "original"
        pipeline.translate_generated()
        waited = 0.0
        while waited < 20.0 and pipeline.is_busy():
            app.processEvents()
            time.sleep(0.2)
            waited += 0.2
        app.processEvents()
        expect("生成的字幕能单独翻译（不重新转写）",
               bool(pipeline.cues) and all(pipeline.translated),
               pipeline.translated[:3])
        rendered = pipeline.output_path.read_text(encoding="utf-8") if pipeline.output_path else ""
        expect("翻译生成字幕后 ASS 出现译文行",
               any(",Trans," in ln for ln in rendered.splitlines()
                   if ln.startswith("Dialogue:")))
        expect("翻译生成字幕时生成轨仍被选中",
               pipeline.ai_pid is not None and engine.get("sid") == pipeline.ai_pid,
               f"{engine.get('sid')} vs {pipeline.ai_pid}")
    finally:
        translate.build = original_build

    # 阶段二：实时流（窗口调小，8 秒素材也能看到窗口推进）
    pipeline.stop_all()
    config.set("asr.window", 6.0)
    config.set("asr.overlap", 1.0)
    # 8 秒素材给 30 秒预读会在测试检查期间直接跑到片尾，回跳就没得测了
    config.set("asr.live_lead", 2.0)
    config.set("languages.subtitle_display", "bilingual")
    pipeline.begin_media(media)
    engine.seek(0.0, absolute=True)
    engine.set("pause", False)
    errors.clear()
    live_build = translate.build
    translate.build = lambda _config, live=False: GenStub()
    try:
        pipeline.generate_ai(live=True, translate=True)
        samples: list[int] = []
        waited = 0.0
        while waited < 60.0:
            app.processEvents()
            time.sleep(0.5)
            waited += 0.5
            samples.append(len(pipeline.cues))
            if len(pipeline.cues) >= 3 and pipeline.ai_pid is not None:
                break
        expect(f"实时模式产出句子（{waited:.0f}s）", len(pipeline.cues) >= 1, samples[-3:])
        expect("实时模式是渐进产出的", len(set(samples)) > 1, samples)
        expect("实时字幕挂成一条轨", pipeline.ai_pid is not None, str(pipeline.ai_pid))
        expect("实时字幕写在临时目录，不进字幕库",
               pipeline.output_path is not None
               and cfg.LIVE_DIR in pipeline.output_path.parents,
               str(pipeline.output_path))
        deadline = time.time() + 20
        while time.time() < deadline and not any(pipeline.translated):
            app.processEvents()
            time.sleep(0.3)
        expect("实时翻译会产出译文", any(pipeline.translated), pipeline.translated[:3])
        live_written = (pipeline.output_path.read_text(encoding="utf-8")
                        if pipeline.output_path else "")
        expect("实时翻译写进生成轨",
               any(",Trans," in ln for ln in live_written.splitlines()
                   if ln.startswith("Dialogue:")))
        duration = engine.duration or 8.0
        expect("实时窗口没有跑过片尾", all(c.start <= duration + 1.5 for c in pipeline.cues),
               [round(c.start, 1) for c in pipeline.cues])
        before_seek = len(pipeline.cues)
        engine.seek(0.2, absolute=True)
        deadline = time.time() + 15
        while time.time() < deadline and not any(
                "回跳" in note or "重新预读" in note for note in notices):
            app.processEvents()
            time.sleep(0.3)
        expect("回跳后不报错、已有句子不丢", len(pipeline.cues) >= before_seek and not errors,
               f"{before_seek}→{len(pipeline.cues)} errors={errors}")
        expect("回跳后实时翻译会重新预读",
               any("回跳" in note or "重新预读" in note for note in notices), notices[-4:])
        live_path = pipeline.output_path
        pipeline.stop_all()
        expect("停止实时后临时字幕文件被清掉",
               live_path is not None and not live_path.exists(), str(live_path))
        expect("停止实时后输出路径回到正常字幕目录",
               pipeline.output_path is not None and cfg.LIVE_DIR not in pipeline.output_path.parents,
               str(pipeline.output_path))
    finally:
        translate.build = live_build

    # 没有密钥时切双语：必须报错提示，不能崩、不能静默
    from lumina.pipeline import SourceInfo
    if pipeline.source is None and pipeline.cues:
        pipeline.source = SourceInfo(list(pipeline.cues), "en", "ai", pipeline.output_path)
    errors.clear()
    cfg.secret_delete("openai")
    config.set("languages.subtitle_display", "bilingual")
    pipeline.target = "zh-CN"
    pipeline.set_display("bilingual")
    waited = 0.0
    while waited < 25.0 and not errors:
        app.processEvents()
        time.sleep(0.3)
        waited += 0.3
    expect("缺密钥时给出可见错误而不是静默", bool(errors), errors)
    expect("错误信息里说要填密钥",
           any("密钥" in message for message in errors), errors)

    # 成对的另一半：显式切双语该报错，但点「整片/实时转写」不能被翻译密钥卡死 ——
    # 本地 whisper 与密钥无关，缺接口时必须降级成只转写原文，而不是抛异常没开始跑
    errors.clear()
    notices.clear()
    pipeline.mode = "bilingual"
    pipeline.generate_ai(live=True)
    waited = 0.0
    while waited < 30.0 and not any("只转写原文" in n for n in notices):
        app.processEvents()
        time.sleep(0.4)
        waited += 0.4
    pipeline.stop_all()
    app.processEvents()
    expect("缺密钥时转写任务降级继续（不是抛错没开始）",
           any("只转写原文" in n for n in notices) and not errors,
           f"notices={notices} errors={errors}")

    pipeline.stop_all()
    engine.quit()
    (cfg.SUPPORT_DIR / "verify_ai.toml").unlink(missing_ok=True)
    print("\nAIFLOW PASS" if not FAILURES else f"\nAIFLOW FAIL: {FAILURES}")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
