#!/usr/bin/env python
"""UI 交互验证：真的去点按钮/切列表，再看 mpv 状态有没有跟着变。

不起可见窗口（把 mpv 的 vo 换成 gpu+ao=null），所以不会在桌面上闪出播放窗。
跑法：uv run python scripts/verify_ui_actions.py
"""
from __future__ import annotations

import faulthandler
import os
import sys
import time
from pathlib import Path

# 卡住时把每个线程的栈打出来，别让人对着没有输出的终端干等
faulthandler.dump_traceback_later(150, exit=True)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from lumina import asr, config as cfg, engine as mpv_engine, storage, translate  # noqa: E402
from lumina.pipeline import SourceInfo  # noqa: E402
from lumina.subtitles import Cue  # noqa: E402
from lumina.ui import panels  # noqa: E402
from lumina.ui.main_window import MainWindow  # noqa: E402

MEDIA = cfg.WORK_DIR / "fixture" / "multi.mkv"
FAILURES: list[str] = []


def expect(name: str, condition, detail: str = "") -> bool:
    ok = bool(condition)
    print(f"[{'OK  ' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)
    return ok


def settle(app: QApplication, seconds: float = 0.35) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


class StubTranslator(translate.Translator):
    name = "stub"
    model_label = "stub"
    delay = 0.0

    def translate(self, texts, target, source=None, context=None):
        if type(self).delay:
            time.sleep(type(self).delay)      # 给「任务运行中」的判据留出观察窗口
        return [f"[{t}]" for t in texts]


def main() -> int:
    app = QApplication([])
    if not MEDIA.exists():
        import subprocess
        subprocess.run([sys.executable, str(ROOT / "scripts" / "selfcheck.py"),
                        "--fixture-only"], check=True)

    config = cfg.Config(cfg.SUPPORT_DIR / "verify_ui.toml")
    config.set("player.backend", "ipc")
    config.set("cloud.start_mode", "manual")   # 测试进程不许启停/换血用户的网关
    config.set("player.embed_mpv", False)
    config.set("translate.cache", False)   # 别让旧译文缓存吞掉桩翻译的耗时
    ui_subtitles = cfg.WORK_DIR / "verify_ui_subtitles"
    config.set("subtitle.output_dir", str(ui_subtitles))
    import shutil
    shutil.rmtree(ui_subtitles, ignore_errors=True)   # 别自动挂回上一轮的字幕
    window = MainWindow(config)
    # 无窗跑：保留 vo=gpu 才能真的走硬解，ao=null 静音
    real_launch = window.engine.launch
    window.engine.launch = lambda wid=None, extra=(): real_launch(
        wid=None, extra=("--vo=gpu", "--ao=null", "--force-window=no"))
    window.resize(1100, 700)
    window.show()
    settle(app, 1.2)
    expect("引擎连上", window.engine.is_alive() and window._engine_ready,
           f"alive={window.engine.is_alive()} ready={window._engine_ready}")

    # --- 打开文件（走 UI 的公开入口，不是直接调 engine）
    window.open_path(str(MEDIA))
    deadline = time.time() + 12
    while time.time() < deadline and not window.engine.tracks():
        settle(app, 0.1)
    expect("点开后有轨道", len(window.engine.tracks()) >= 5, len(window.engine.tracks()))
    expect("首屏卡片被收掉", not window.welcome.isVisible(), str(window.welcome.isVisible()))
    expect("标题同步到浮层", window.overlay.title_label.text() == MEDIA.name,
           window.overlay.title_label.text())

    # --- 播放/暂停按钮
    was_paused = bool(window.engine.get("pause"))
    window.overlay.bar.play_button.click()
    settle(app)
    expect("点播放/暂停真的切了状态", bool(window.engine.get("pause")) != was_paused,
           f"{was_paused} → {window.engine.get('pause')}")
    expect("按钮图标跟着状态换", window.overlay.bar.play_button.icon().availableSizes(),
           "无图标")
    window.overlay.bar.play_button.click()
    settle(app)

    # --- 音量 / 静音 / 倍速
    window.overlay.bar.volume_slider.setValue(73)
    settle(app)
    expect("拖音量条改到引擎", abs(float(window.engine.get("volume")) - 73) < 1.5,
           window.engine.get("volume"))
    cfg_volume = int(float(window.config.get("player.volume", 100)))
    target_volume = 42 if cfg_volume != 42 else 44
    window._set_volume(target_volume)
    settle(app, 0.05)
    expect("音量立刻落到引擎（不等配置写盘）",
           abs(float(window.engine.get("volume")) - target_volume) < 1.5,
           window.engine.get("volume"))
    expect("音量配置合并到停顿后才写盘",
           int(float(window.config.get("player.volume", -1))) != target_volume,
           window.config.get("player.volume"))
    settle(app, 0.5)
    expect("停顿后音量写进配置",
           int(float(window.config.get("player.volume", -1))) == target_volume,
           window.config.get("player.volume"))
    window.overlay.bar.mute_button.click()
    settle(app)
    expect("点静音按钮生效", bool(window.engine.get("mute")), window.engine.get("mute"))
    window.overlay.bar.mute_button.click()
    settle(app)
    window.overlay.bar.speed_button.menu().actions()[5].trigger()
    settle(app)
    expect("倍速菜单选择生效", abs(float(window.engine.get("speed")) - 1.5) < 0.01,
           window.engine.get("speed"))
    window.overlay.bar.speed_button.click()
    settle(app)
    expect("倍速按钮点击按步长走", abs(float(window.engine.get("speed")) - 1.75) < 0.01,
           window.engine.get("speed"))

    # --- 进度条：拖动结束信号要落到 seek
    bar = window.overlay.bar.seek
    bar.set_range(window.engine.duration)
    settle(app)
    bar.seek_requested.emit(6.0)
    settle(app, 0.3)
    window.engine.poll_position()
    landed = window.engine.position or 0.0
    expect("进度条定位到 6 秒附近", 5.9 <= landed <= 7.6, landed)

    # --- 侧栏：切音轨
    window.show_sidebar("tracks")
    settle(app, 0.5)
    expect("侧栏可见", window.sidebar.isVisible())
    tracks_panel = window.sidebar.tracks
    expect("轨道栏有视频/音轨/字幕/显示方式四个下拉",
           all(hasattr(tracks_panel, name) for name in
               ("video_combo", "audio_combo", "sub_combo", "mode_combo")))
    expect("侧栏有 6 个标签，网盘页和本地播放共用 MediaSource",
           hasattr(window.sidebar.cloud_browser, "open_source")
           and window.sidebar.stack.count() == 6
           and [b.text() for b in window.sidebar.buttons] == list(panels.Sidebar.PAGES),
           (window.sidebar.stack.count(),
            [b.text() for b in window.sidebar.buttons]))
    cramped = [f"{b.text()}:{b.width()}<{b.sizeHint().width()}"
               for b in window.sidebar.buttons
               if b.width() < b.sizeHint().width()
               or b.x() + b.width() > window.sidebar.width()]
    expect("标签排成 3 列 2 行，没有被挤成省略号（宽度≥sizeHint 且不越界）",
           not cramped and window.sidebar.buttons[0].y() != window.sidebar.buttons[3].y(),
           (cramped, [(b.text(), b.width(), b.sizeHint().width(), b.x(), b.y())
                      for b in window.sidebar.buttons]))
    window.show_sidebar("115")
    settle(app)
    expect("网盘标签默认落在 115 浏览", window.sidebar.cloud.stack.currentIndex() == 0,
           window.sidebar.cloud.stack.currentIndex())
    window.show_sidebar("cloud_manage")
    settle(app)
    manage = window.sidebar.cloud_manage
    expect("OpenList 管理并进的网盘标签里，分段开关能切过去",
           window.sidebar.cloud.stack.currentIndex() == 1 and manage.isVisible()
           and all(hasattr(manage, name) for name in
                   ("scan_button", "save_storage_button", "cache_combo", "restart_button")),
           (window.sidebar.cloud.stack.currentIndex(), manage.isVisible()))
    expect("网盘分段开关两项都在（115 文件 / OpenList 服务）",
           [b.text() for b in window.sidebar.cloud.buttons]
           == ["115 文件", "OpenList 服务"],
           [b.text() for b in window.sidebar.cloud.buttons])
    expect("管理页与主窗口用的是同一个网关客户端（管的确实是软件自己那个）",
           window.sidebar.cloud.client is window.cloud_client
           and manage.client is window.cloud_client)
    expect("网关出厂默认是随软件启动，勾选框如实反映配置",
           cfg.DEFAULTS["cloud"]["start_mode"] == "auto"
           and manage.autostart_check.isChecked() is False,
           (cfg.DEFAULTS["cloud"]["start_mode"], config.get("cloud.start_mode"),
            manage.autostart_check.isChecked()))
    manage.autostart_check.setChecked(False)
    manage._pick_autostart()
    expect("取消勾选就改成手动启动", config.get("cloud.start_mode") == "manual",
           config.get("cloud.start_mode"))
    manage.autostart_check.setChecked(True)
    manage._pick_autostart()
    expect("勾回来又恢复随软件启动的策略",
           config.get("cloud.start_mode") == "auto" and window.cloud_client.autostart_wanted(),
           config.get("cloud.start_mode"))
    # 收尾改回手动：这个测试进程没启动过网关，退出时不该动用户的实例
    manage.autostart_check.setChecked(False)
    manage._pick_autostart()
    # 页内切分段开关也要触发状态读取：曾经点进「OpenList 服务」永远停在「读取状态…」
    manage.status_label.setText("读取状态…")
    window.sidebar.cloud.show_subpage(0)
    settle(app, 0.2)
    window.sidebar.cloud.show_subpage(1)
    settle(app, 1.5)
    expect("切到服务页就开始读状态，并立刻写明网关来源",
           manage.status_label.text() != "读取状态…"
           and "网关程序" in manage.status_label.text(),
           manage.status_label.text()[:80])
    saved_status = manage.client.status
    manage.client.status = lambda: (_ for _ in ()).throw(RuntimeError("端口不通"))
    manage.refresh()
    settle(app, 1.5)
    manage.client.status = saved_status
    expect("状态读不出来时也写在状态行上（不许干等一句「读取中」）",
           "状态读取失败" in manage.status_label.text()
           and "网关程序" in manage.status_label.text(),
           manage.status_label.text()[:80])
    manage.refresh()
    settle(app, 1.5)
    audio = tracks_panel.audio_combo
    expect("音轨下拉填了两条", audio.count() == 2, audio.count())
    expect("音轨下拉显示的就是 mpv 当前选中的轨",
           audio.currentData() == window.engine.get("aid"),
           f"{audio.currentData()} vs {window.engine.get('aid')}")

    def pick(combo, index: int) -> None:
        """模拟用户选一项：Qt 是先改 currentIndex 再发 activated，
        手动 emit 不会自己挪索引，所以两步都要做。"""
        combo.setCurrentIndex(index)
        combo.activated.emit(index)

    pick(audio, 0)
    settle(app)
    expect("选下拉第 1 项 → aid 跟着变", window.engine.get("aid") == audio.itemData(0),
           f"{window.engine.get('aid')} vs {audio.itemData(0)}")
    pick(audio, 1)
    settle(app)
    expect("选第 2 项也生效", window.engine.get("aid") == audio.itemData(1),
           window.engine.get("aid"))
    settle(app, 1.5)
    expect("手动选的音轨不会被默认语言抢回去",
           window.engine.get("aid") == audio.itemData(1), window.engine.get("aid"))

    # --- 侧栏：字幕轨 + 双语翻译（用桩翻译器，不联网）
    def pick_mode(value: str) -> None:
        """走 activated，才能覆盖「重复选同一项也要重试」这条路。"""
        pick(tracks_panel.mode_combo, tracks_panel.mode_combo.findData(value))

    subs = window.engine.tracks_of("sub")
    zh = next((t for t in subs if (t.get("lang") or "").startswith(("chi", "zho", "zh"))), None)
    expect("字幕下拉里有中文轨", zh is not None and tracks_panel.sub_combo.findData(zh.get("id")) >= 0,
           [t.get("lang") for t in subs])
    expect("字幕下拉显示的就是 mpv 当前 sid",
           tracks_panel.sub_combo.currentData() == window.engine.get("sid"),
           f"{tracks_panel.sub_combo.currentData()} vs {window.engine.get('sid')}")
    en = next((t for t in subs if (t.get("lang") or "").startswith("en")), None)
    if en is not None:
        pick(tracks_panel.sub_combo, tracks_panel.sub_combo.findData(en.get("id")))
        settle(app, 1.5)
        expect("手动选的字幕不会被默认语言抢回去",
               window.engine.get("sid") == en.get("id"), window.engine.get("sid"))
    pick(tracks_panel.sub_combo, tracks_panel.sub_combo.findData(zh.get("id")))
    settle(app)
    expect("选字幕轨 → sid 生效", window.engine.get("sid") == zh.get("id"),
           f"{window.engine.get('sid')} vs {zh.get('id')}")
    expect("「关闭字幕」是下拉第一项", tracks_panel.sub_combo.itemData(0) == "no",
           tracks_panel.sub_combo.itemData(0))

    original_build = translate.build
    translate.build = lambda _config, live=False: StubTranslator()
    try:
        StubTranslator.delay = 0.6
        for stale in (window.pipeline.output_path, window.pipeline.original_path,
                      window.pipeline.translated_path()):
            if stale and stale.exists():
                stale.unlink()      # 别把上一轮自检留下的文件当成本轮成果
        pick_mode("bilingual")
        deadline = time.time() + 30
        seen_task_bar = False
        seen_spinner = False
        while time.time() < deadline and window.pipeline.ai_pid is None:
            settle(app, 0.2)
            task_box = window.sidebar.ai.task_box
            seen_task_bar = seen_task_bar or not task_box.isHidden()
            seen_spinner = seen_spinner or (not task_box.isHidden()
                                            and window.sidebar.ai.progress.maximum() == 0)
        expect("任务运行时任务进度条才出现", seen_task_bar)
        expect("第一批完成前进度条转圈，而不是定在 0%", seen_spinner)
        expect("选「双语」→ 生成轨挂上", window.pipeline.ai_pid is not None,
               str(window.pipeline.ai_pid))
        expect("生成轨成为当前字幕", window.engine.get("sid") == window.pipeline.ai_pid,
               f"{window.engine.get('sid')} vs {window.pipeline.ai_pid}")
        settle(app, 1.0)
        written = window.pipeline.output_path.read_text(encoding="utf-8")
        expect("双语文件里同时有原文与译文", "[原文]" in written and "[译文]" in written
               or ("Main," in written and "Trans," in written), written[:120])
        # 三份产物：文件夹与文件名都必须是「视频名 + 说明后缀」
        pipe = window.pipeline
        expect("每部片一个文件夹，文件夹名就是视频名",
               pipe.sub_folder.name == cfg.safe_name(Path(MEDIA).stem), str(pipe.sub_folder))
        expect("双语轨叫 <视频名>.双语.ass",
               pipe.output_path.name == f"{Path(MEDIA).stem}.双语.ass", pipe.output_path.name)
        raw_path = pipe.original_path
        raw = raw_path.read_text(encoding="utf-8") if raw_path and raw_path.exists() else ""
        expect("原文 .srt 落在同目录且只有原文",
               bool(raw_path) and raw_path.name.endswith(".原文.srt") and "-->" in raw
               and "[译文]" not in raw, f"{raw_path} / {raw[:60]!r}")
        tpath = pipe.translated_path()
        traw = tpath.read_text(encoding="utf-8") if tpath.exists() else ""
        expect("译文 .srt 文件名带目标语言且只有译文",
               "中文译文" in tpath.name and "-->" in traw and "[原文]" not in traw,
               f"{tpath.name} / {traw[:60]!r}")
        tracks_panel.reload()
        settle(app, 0.4)
        ai_entries = [i for i in range(tracks_panel.sub_combo.count())
                      if tracks_panel.sub_combo.itemData(i) == pipe.ai_pid]
        expect("音频生成的 AI 字幕轨在下拉里（且只列一条）", len(ai_entries) == 1,
               [(tracks_panel.sub_combo.itemData(i), tracks_panel.sub_combo.itemText(i))
                for i in range(tracks_panel.sub_combo.count())])
        if ai_entries:
            pick(tracks_panel.sub_combo, ai_entries[0])
            settle(app, 1.2)
            expect("AI 字幕轨可以被选中且不会被抢回去",
                   window.engine.get("sid") == pipe.ai_pid,
                   f"{window.engine.get('sid')} vs {pipe.ai_pid}")
            deadline = time.time() + 20
            while time.time() < deadline and window.pipeline.is_busy():
                settle(app, 0.2)
            translated_generated = []
            original_tg = window.pipeline.translate_generated
            window.pipeline.translate_generated = lambda: translated_generated.append(True)
            try:
                window.sidebar.ai._request_translate()
                settle(app, 0.2)
                expect("生成出来的字幕也能作为翻译对象",
                       bool(translated_generated), translated_generated)
            finally:
                window.pipeline.translate_generated = original_tg
        pick_mode("translated")
        settle(app, 1.2)
        only = window.pipeline.output_path.read_text(encoding="utf-8")
        expect("点「只看译文」后只剩 Trans 行",
               all(",Trans," in ln for ln in only.splitlines() if ln.startswith("Dialogue:")))
        pick_mode("original")
        settle(app, 1.0)
        expect("点「只看原文」回到原生轨", window.engine.get("sid") == zh.get("id"),
               window.engine.get("sid"))
    finally:
        translate.build = original_build
        StubTranslator.delay = 0.0

    # --- AI 栏：引擎下拉必须整组显隐（曾经只藏控件、留下一个光杆分区标题）
    window.show_sidebar("ai")
    settle(app)
    ai = window.sidebar.ai

    window.pipeline.set_language_hint("ja")
    ai.sync_language()
    expect("识别语言按本片保存并同步到下拉",
           ai.lang_combo.currentData() == "ja" and "本片指定" in ai.lang_note.text(),
           (ai.lang_combo.currentData(), ai.lang_note.text()))
    window.pipeline.set_language_hint("auto")
    ai.sync_language()
    expect("切回自动后下拉回到自动检测",
           ai.lang_combo.currentData() == "auto", ai.lang_combo.currentData())

    deadline = time.time() + 20
    while time.time() < deadline and window.pipeline.is_busy():
        settle(app, 0.2)
    expect("AI 栏四个动作按钮：转字幕 / 字幕翻译 / 一键翻译 / 实时翻译",
           (ai.full_button.text(), ai.translate_button.text(),
            ai.one_click_button.text(), ai.live_button.text())
           == ("转字幕", "字幕翻译", "一键翻译", "实时翻译"),
           (ai.full_button.text(), ai.translate_button.text(),
            ai.one_click_button.text(), ai.live_button.text()))
    expect("四个动作按钮排在一起（两行两列）",
           ai.full_button.y() == ai.one_click_button.y()
           and ai.translate_button.y() == ai.live_button.y()
           and ai.translate_button.y() > ai.full_button.y(),
           [(b.text(), b.x(), b.y()) for b in (ai.full_button, ai.one_click_button,
                                               ai.translate_button, ai.live_button)])
    calls: list[tuple[bool, bool]] = []
    original_generate = window.pipeline.generate_ai
    window.pipeline.generate_ai = lambda live=False, translate=True: calls.append(
        (live, translate))
    try:
        ai.full_button.click()
        settle(app, 0.2)
        ai.one_click_button.click()
        settle(app, 0.2)
        ai.live_button.setChecked(True)
        ai._toggle_live()
        settle(app, 0.2)
        expect("「转字幕」按钮只转写不翻译", calls[:1] == [(False, False)], calls)
        expect("「一键翻译」按钮转写+翻译", calls[1:2] == [(False, True)], calls)
        expect("勾「实时翻译」走实时转写 + 翻译", calls[2:3] == [(True, True)], calls)
        before = len(calls)
        ai.live_button.setChecked(False)
        ai._toggle_live()
        settle(app, 0.2)
        expect("取消勾选「实时翻译」是停止，不会启动整片转写",
               len(calls) == before, calls)
    finally:
        window.pipeline.generate_ai = original_generate
        ai.live_button.setChecked(False)

    # --- 一键翻译：旧版/半截转写不能被当成完整转写直接翻译
    saved = {
        "ai_pid": window.pipeline.ai_pid,
        "cues": window.pipeline.cues,
        "translated": window.pipeline.translated,
        "source": window.pipeline.source,
        "_transcript_verified": window.pipeline._transcript_verified,
    }
    marker = window.pipeline._marker_path()
    if marker:
        marker.unlink(missing_ok=True)
    window.pipeline.cues = [Cue(0.0, 1.0, ["legacy"])]
    window.pipeline.translated = [""]
    window.pipeline.source = SourceInfo(list(window.pipeline.cues), "en", "ai", None)
    window.pipeline.ai_pid = 999
    window.pipeline._transcript_verified = False
    decisions: list[str] = []
    original_gen = window.pipeline.generate_ai
    original_tg = window.pipeline.translate_generated
    window.pipeline.generate_ai = lambda live=False, translate=True: decisions.append("asr")
    window.pipeline.translate_generated = lambda: decisions.append("translate")
    try:
        window._request_one_click()
        settle(app, 0.2)
        expect("旧版/半截字幕不会被一键翻译直接复用", decisions[-1:] == ["asr"], decisions)
        window.pipeline._write_marker(True)
        window.pipeline._transcript_verified = False
        window._request_one_click()
        settle(app, 0.2)
        expect("有完成标记的转写才会被一键翻译复用",
               decisions[-1:] == ["translate"], decisions)
    finally:
        window.pipeline.generate_ai = original_gen
        window.pipeline.translate_generated = original_tg
        for key, value in saved.items():
            setattr(window.pipeline, key, value)
        if marker:
            marker.unlink(missing_ok=True)

    ai.context_combo.setCurrentIndex(ai.context_combo.findData(6))
    ai._pick_context()
    expect("翻译上下文条数可设置",
           int(window.config.get("translate.context_lines", 0)) == 6,
           window.config.get("translate.context_lines"))
    ai.context_combo.setCurrentIndex(ai.context_combo.findData(0))
    ai._pick_context()

    def pick_engine(value: str) -> None:
        index = ai.engine_combo.findData(value)
        ai.engine_combo.setCurrentIndex(index)
        ai.engine_combo.activated.emit(index)
        settle(app)

    expect("AI 栏有本地 llama 引擎可选", ai.engine_combo.findData("llama") >= 0,
           [ai.engine_combo.itemText(i) for i in range(ai.engine_combo.count())])
    pick_engine("llama")
    expect("选本地引擎 → GGUF 组整组出现", ai.llm_box.isVisible() and not ai.online_box.isVisible()
           and not ai.key_box.isVisible(),
           f"llm={ai.llm_box.isVisible()} online={ai.online_box.isVisible()} key={ai.key_box.isVisible()}")
    labels = [x for x in ai.llm_box.findChildren(QLabel) if "GGUF" in x.text()]
    expect("分区标题在 GGUF 组里面（跟着一起藏）", bool(labels), len(labels))
    pick_engine("openai")
    expect("选在线引擎 → 接口组出现、GGUF 组整组消失",
           ai.online_box.isVisible() and not ai.llm_box.isVisible() and ai.key_box.isVisible(),
           f"llm={ai.llm_box.isVisible()} online={ai.online_box.isVisible()}")
    pick_engine("deepl")
    expect("选 DeepL → 两组都不出现（不留光杆标题）",
           not ai.llm_box.isVisible() and not ai.online_box.isVisible() and ai.key_box.isVisible(),
           f"llm={ai.llm_box.isVisible()} online={ai.online_box.isVisible()}")
    pick_engine("llama")
    fm = ai.llm_combo.fontMetrics()
    room = ai.llm_combo.width() - 44          # 44 = 下拉箭头 + 左右内边距
    clipped = [ai.llm_combo.itemText(i) for i in range(ai.llm_combo.count())
               if fm.horizontalAdvance(ai.llm_combo.itemText(i)) > room]
    expect("GGUF 下拉的型号文字不被截断（放得进合上的框）", not clipped, clipped)
    pick_engine(str(window.config.get("translate.engine", "openai")))

    # --- AI 栏：下载 / 测试都在后台线程跑，回 GUI 只能走信号
    #     曾经 worker 直接 setText/setValue，下载 2 秒后整个进程 abort（QOpenGLContext 跨线程）
    expect("模型目录是软件数据目录下的独立文件夹",
           cfg.MODEL_DIR.parent == cfg.SUPPORT_DIR
           and cfg.ASR_MODEL_DIR.parent == cfg.MODEL_DIR
           and cfg.LLM_MODEL_DIR.parent == cfg.MODEL_DIR,
           f"{cfg.MODEL_DIR} / {cfg.ASR_MODEL_DIR} / {cfg.LLM_MODEL_DIR}")
    panel = window.sidebar.storage
    window.show_sidebar("storage")
    # 体积是一行一行后台算完再填的（整个数据目录 + 构建缓存要几秒），给它一个上限等到齐
    deadline = time.time() + 30
    while time.time() < deadline and "应用数据目录共" not in panel.total_label.text():
        settle(app, 0.3)
    listed = set(panel._sizes)
    wanted_paths = {str(cfg.SUPPORT_DIR), str(cfg.CONFIG_PATH), str(cfg.WORK_DIR),
                    str(cfg.LIVE_DIR), str(cfg.LANGUAGE_HINTS_PATH),
                    str(cfg.APP_LOG_PATH), str(cfg.LOG_PATH),
                    str(storage.translation_cache_path()), str(storage.transcript_dir()),
                    str(storage.subtitle_root(config)), str(storage.openlist_dir(config)),
                    str(cfg.ASR_MODEL_DIR), str(cfg.LLM_MODEL_DIR)}
    expect("存储栏把产出/下载/缓存/日志/网关路径全部列出来",
           wanted_paths <= listed, sorted(wanted_paths - listed))
    expect("存储栏显示总占用与模型占比",
           "应用数据目录共" in panel.total_label.text()
           and "模型" in panel.total_label.text(), panel.total_label.text())
    model_rows = [row for row in storage.installed_models(config)
                  if str(row.path) in listed]
    expect("已下载模型逐个成行（含体积）",
           len(model_rows) == len(storage.installed_models(config))
           and all(panel._sizes[str(row.path)].text() not in ("", "统计中…")
                   for row in model_rows),
           [(row.name, panel._sizes.get(str(row.path))) for row in model_rows][:3])
    expect("字幕/模型文件夹已经从 AI 栏搬进设置页",
           hasattr(window.sidebar.settings, "dir_edit")
           and not hasattr(ai, "dir_edit") and not hasattr(ai, "model_dir_note"))
    settings = window.sidebar.settings
    settings.next_to_video.setChecked(True)
    settings._pick_next_to_video()
    expect("设置里能切换「字幕放在视频旁边」",
           bool(config.get("subtitle.next_to_video")) and not settings.dir_edit.isEnabled(),
           window.config.get("subtitle.next_to_video"))
    settings.next_to_video.setChecked(False)
    settings._pick_next_to_video()
    expect("取消跟随后固定目录输入恢复可用",
           config.get("subtitle.next_to_video") is False and settings.dir_edit.isEnabled())
    settings.lead_combo.setCurrentIndex(settings.lead_combo.findData(10))
    settings._pick_live_lead()
    expect("实时翻译预读时间可设置",
           float(config.get("asr.live_lead", 0)) == 10.0, config.get("asr.live_lead"))
    settings.lead_combo.setCurrentIndex(settings.lead_combo.findData(0))
    settings._pick_live_lead()
    expect("预读可选「整片（尽可能超前）」",
           float(config.get("asr.live_lead", -1)) == 0.0, config.get("asr.live_lead"))
    settings.lead_combo.setCurrentIndex(settings.lead_combo.findData(30))
    settings._pick_live_lead()

    pick_engine("llama")
    asr_have = ai.model_combo.currentText().split("  ")[0] in asr.list_models()
    expect("转写模型已存在时按钮显示「模型已就绪」且不可点",
           (not ai.download_button.isEnabled() and "已就绪" in ai.download_button.text())
           if asr_have else ai.download_button.isEnabled(),
           ai.download_button.text())

    expect("本地模型分成「整片」和「实时」两个独立下拉",
           hasattr(ai, "llm_live_combo")
           and ai.llm_live_combo.count() == ai.llm_combo.count(),
           (ai.llm_combo.count(), getattr(ai, "llm_live_combo", None)))
    expect("Qwen3.5-9B 已加入本地翻译模型列表",
           any("Qwen3.5-9B" in ai.llm_combo.itemText(i)
               for i in range(ai.llm_combo.count())),
           [ai.llm_combo.itemText(i) for i in range(ai.llm_combo.count())])
    live_model = "qwen2.5-0.5b-instruct-q4_k_m.gguf"
    if translate.llm_model_ready(live_model):
        ai.llm_combo.setCurrentIndex(
            ai.llm_combo.findData("qwen2.5-1.5b-instruct-q4_k_m.gguf"))
        ai.llm_live_combo.setCurrentIndex(ai.llm_live_combo.findData(live_model))
        settle(app, 0.2)
        expect("整片/实时模型分别保存",
               config.get("translate.llama_model")
               == "qwen2.5-1.5b-instruct-q4_k_m.gguf"
               and config.get("translate.live_llama_model") == live_model,
               (config.get("translate.llama_model"),
                config.get("translate.live_llama_model")))
        expect("实时模型就绪时按钮写明已就绪",
               not ai.llm_live_download.isEnabled()
               and "已就绪" in ai.llm_live_download.text(),
               ai.llm_live_download.text())

    ai._download_button = ai.llm_live_download
    ai._download_bar = ai.llm_live_dl_bar
    ai._download_note = None
    try:
        ai.llm_live_dl_bar.show()
        ai.download_tick.emit(8_000_000_000, 9_000_000_000)
        settle(app, 0.1)
        expect("大模型下载进度不会超过 100%（9 GB 走 64 位字节数）",
               ai.llm_live_download.text().endswith("88%")
               and ai.llm_live_dl_bar.value() == 88,
               (ai.llm_live_download.text(), ai.llm_live_dl_bar.value()))
    finally:
        ai._download_button = None
        ai._download_bar = None
        ai._download_note = None
        ai.llm_live_dl_bar.hide()
        ai.refresh()

    ready_model = "qwen2.5-1.5b-instruct-q4_k_m.gguf"
    if translate.llm_model_ready(ready_model):
        ai.llm_combo.setCurrentIndex(ai.llm_combo.findData(ready_model))
        settle(app, 0.2)
        expect("已有 GGUF 模型时按钮显示「模型已就绪」且不可点",
               not ai.llm_download.isEnabled() and "已就绪" in ai.llm_download.text(),
               ai.llm_download.text())
    previous_model = ai.llm_combo.currentData()
    missing_model = "qwen2.5-3b-instruct-q4_k_m.gguf"
    original_ready = translate.llm_model_ready
    # 本机可能已经把 3B 也下完了；这里模拟“未下载”，专门验按钮会变成下载
    translate.llm_model_ready = lambda name: (False if name == missing_model
                                              else original_ready(name))
    try:
        ai.llm_combo.setCurrentIndex(ai.llm_combo.findData(missing_model))
        settle(app, 0.2)
        expect("选中未下载的 GGUF 模型时下载按钮可用",
               ai.llm_download.isEnabled(), ai.llm_download.text())
        expect("未下载的 GGUF 模型按钮明确写「下载」",
               "下载" in ai.llm_download.text(), ai.llm_download.text())

        original_download = translate.download_llm_model
        top_before = ai.progress.value()

        def stub_download(name, on_progress=None, cancel=None, on_note=None):
            for step in range(1, 6):
                if on_progress:
                    on_progress(step * 20, 100)
                time.sleep(0.08)

        translate.download_llm_model = stub_download
        try:
            ai.llm_download.click()
            deadline = time.time() + 5
            seen_bar = False
            while time.time() < deadline and "下载完成" not in ai.llm_dl_note.text():
                settle(app, 0.05)
                seen_bar = seen_bar or ai.llm_dl_bar.isVisible()
            expect("下载进度条就出现在按钮下面（不再借用转写进度条）",
                   seen_bar or ai.llm_dl_bar.value() == 100)
            expect("下载完成后按钮旁写明结果",
                   "下载完成" in ai.llm_dl_note.text(), ai.llm_dl_note.text())
            expect("本地下载进度条走到 100%", ai.llm_dl_bar.value() == 100,
                   ai.llm_dl_bar.value())
            expect("模型下载不占用转写进度条", ai.progress.value() == top_before,
                   f"{top_before} → {ai.progress.value()}")
            expect("下载结束后按钮恢复可点", ai.llm_download.isEnabled())
            settle(app, 1.0)
            expect("下载结束后本地进度条收起", ai.llm_dl_bar.isHidden())
        finally:
            translate.download_llm_model = original_download
    finally:
        translate.llm_model_ready = original_ready
        ai.llm_combo.setCurrentIndex(ai.llm_combo.findData(previous_model))
        settle(app, 0.2)

    # --- 转写模型下载同样用按钮下面的进度，不动转写进度条
    original_asr_download = asr.download_model
    installed_model = ai.model_combo.currentText().split("  ")[0]
    for i in range(ai.model_combo.count()):
        if ai.model_combo.itemText(i).startswith("ggml-tiny-q5_1.bin"):
            ai.model_combo.setCurrentIndex(i)
            break
    settle(app, 0.2)
    expect("选中未下载的转写模型时按钮可用", ai.download_button.isEnabled(),
           ai.download_button.text())
    top_before = ai.progress.value()

    def stub_asr_download(name, on_progress=None, cancel=None, on_note=None):
        for step in range(1, 6):
            if on_progress:
                on_progress(step * 20, 100)
            time.sleep(0.08)

    asr.download_model = stub_asr_download
    try:
        ai.download_button.click()
        deadline = time.time() + 5
        while time.time() < deadline and "下载完成" not in ai.asr_dl_note.text():
            settle(app, 0.05)
        expect("转写模型下载的进度就在按钮下面",
               ai.asr_dl_bar.value() == 100, ai.asr_dl_bar.value())
        expect("转写模型下载不占用转写进度条", ai.progress.value() == top_before,
               f"{top_before} → {ai.progress.value()}")
        settle(app, 1.0)
        expect("转写模型下载结束后本地进度条收起", ai.asr_dl_bar.isHidden())
    finally:
        asr.download_model = original_asr_download
        for i in range(ai.model_combo.count()):
            if ai.model_combo.itemText(i).split("  ")[0] == installed_model:
                ai.model_combo.setCurrentIndex(i)
                break
        settle(app, 0.2)

    translate.build = lambda _config, live=False: StubTranslator()
    try:
        ai.test_button.click()
        deadline = time.time() + 5
        while time.time() < deadline and not ai.test_result.text().startswith(("✓", "✗")):
            settle(app, 0.1)
        expect("「测试翻译」的结果能回到界面（进程不崩）",
               ai.test_result.text().startswith(("✓", "✗")), ai.test_result.text())
        expect("测试结束后按钮恢复可点", ai.test_button.isEnabled())
    finally:
        translate.build = original_build
    pick_engine(str(window.config.get("translate.engine", "openai")))

    deadline = time.time() + 20
    while time.time() < deadline and window.pipeline.is_busy():
        settle(app, 0.2)
    settle(app, 0.5)
    expect("任务结束后任务进度块收起（不再常驻 0%）", ai.task_box.isHidden(),
           f"hidden={ai.task_box.isHidden()}")
    expect("任务结束后保留「上次任务」状态，不再看不出是否在跑",
           "上次任务" in ai.last_task.text() and not ai.last_task.isHidden(),
           ai.last_task.text())

    # --- 快捷键接线（QShortcut 需要真实按键事件，这里调其槽函数验证语义）
    window._cycle_audio_track()
    settle(app)
    expect("切音轨快捷键改了 aid", window.engine.get("aid") in
           [t.get("id") for t in window.engine.tracks_of("audio")], window.engine.get("aid"))
    window._set_speed(1.0)
    settle(app)
    expect("恢复倍速 1.0", abs(float(window.engine.get("speed")) - 1.0) < 0.01)

    # --- 重新打开片子要能自动挂回上次生成的字幕（不用再点一次）
    window.pipeline.stop_all()
    window.pipeline.begin_media(str(MEDIA))
    window._restore_generated(str(MEDIA))
    settle(app, 0.6)
    expect("重新打开片子会自动挂回生成字幕",
           window.pipeline.ai_pid is not None
           and window.engine.get("sid") == window.pipeline.ai_pid,
           f"pid={window.pipeline.ai_pid} sid={window.engine.get('sid')}")
    expect("自动挂回的字幕句数对得上", len(window.pipeline.cues) == 4,
           len(window.pipeline.cues))
    window.pipeline.stop_all()

    # --- 手动把识别语言改成日语后，一键翻译必须重转写，不能复用旧的英语字幕
    calls: list[str] = []
    saved_source = window.pipeline.source
    saved_verified = window.pipeline._transcript_verified
    saved_hint = window.pipeline.language_hint
    saved_generate = window.pipeline.generate_ai
    saved_translate = window.pipeline.translate_generated
    window.pipeline.source = SourceInfo(list(window.pipeline.cues), "en", "ai",
                                        window.pipeline.output_path)
    window.pipeline._transcript_verified = True
    window.pipeline.language_hint = lambda: "ja"
    window.pipeline.generate_ai = lambda **kwargs: calls.append("asr")
    window.pipeline.translate_generated = lambda: calls.append("translate")
    try:
        window._request_one_click()
        expect("改识别语言后一键翻译会重新转写，不是只翻旧字幕",
               calls == ["asr"], calls)
    finally:
        window.pipeline.source = saved_source
        window.pipeline._transcript_verified = saved_verified
        window.pipeline.language_hint = saved_hint
        window.pipeline.generate_ai = saved_generate
        window.pipeline.translate_generated = saved_translate

    # --- 云端实时翻译：必须走单流代理入口，不能再被旧的「下一步接入」门槛拦住
    from lumina.media import MediaSource
    saved_cloud_source = window.pipeline.media_source
    saved_live_entry = window._begin_cloud_live
    live_calls: list[bool] = []
    window.pipeline.media_source = MediaSource(
        uri="http://127.0.0.1:15244/dav/115/x.mp4", display_name="x.mp4",
        key="115:/115/x.mp4", origin="115", headers={})
    window._begin_cloud_live = lambda translate=True: live_calls.append(translate) or True
    try:
        window._request_ai(live=True)
        expect("云端实时翻译走单流代理入口", live_calls == [True], live_calls)
    finally:
        window.pipeline.media_source = saved_cloud_source
        window._begin_cloud_live = saved_live_entry

    # --- 单流代理上的 mpv 预读：默认上百 MB 会把 ASR 的播放头挤出缓存 → 重置风暴
    saved_bytes = window.engine.get("demuxer-max-bytes")
    window.engine._apply_read_ahead(16)
    on_proxy = window.engine.get("demuxer-max-bytes")
    window.engine._apply_read_ahead(None)
    restored = window.engine.get("demuxer-max-bytes")
    expect("走代理时 mpv 预读压到 16 MiB，换回普通播放要还原",
          on_proxy == 16 * 1024 * 1024 and restored == saved_bytes,
          (on_proxy, restored, saved_bytes))

    # --- 实时模型没就绪时必须当场说清楚，而不是开个任务在里面默默不翻
    saved_engine = str(config.get("translate.engine", ""))
    saved_live_model = str(config.get("translate.live_llama_model", ""))
    config.set("translate.engine", "llama")
    config.set("translate.live_llama_model", "zz-没下完的模型.gguf")
    try:
        window._request_ai(live=True)
        settle(app, 0.4)
        note = window.sidebar.ai.last_task.text()
        expect("实时模型没就绪 → 不开任务、面板写明原因",
               not window.pipeline.is_busy() and "没下载" in note, note)
    finally:
        config.set("translate.engine", saved_engine)
        config.set("translate.live_llama_model", saved_live_model)
        window.sidebar.ai.last_task.hide()

    # --- 重复 path 事件：libmpv 在 file-loaded 时会把 path 再发一次，且 http:// 写成 http:/
    #     如果把它当成新素材，云端 source 会被没有 header 的本地 source 顶掉 → ASR 401。
    remote = MediaSource(
        uri="http://127.0.0.1:15244/dav/115/verify.mp4",
        display_name="verify.mp4", key="115:/115/verify.mp4", origin="115",
        headers={"Authorization": "Bearer verify-token"}, user_agent="verify-UA")
    remote.refresh = lambda: {"Authorization": "Bearer refreshed-token"}
    saved_current = window._current
    saved_source = window._current_source
    window._pending_source = remote
    window._media_loaded(remote.uri)
    cloud_folder = window.pipeline.sub_folder
    recent_before = list(window.config.recent)
    window._media_loaded("http:/127.0.0.1:15244/dav/115/verify.mp4")
    settle(app, 0.1)
    expect("重复的 path 事件不把云端 source 换成本地",
           window.pipeline.media_source is not None
           and window.pipeline.media_source.key == "115:/115/verify.mp4",
           getattr(window.pipeline.media_source, "key", None))
    expect("重复 path 事件后仍按云端处理（AI 会走流仲裁）",
           window._is_cloud_source())
    expect("115 URL 不会被塞进最近打开",
           list(window.config.recent) == recent_before, window.config.recent[:2])
    window.pipeline.stop_all()
    window._current = saved_current
    window._current_source = saved_source
    window._pending_source = None
    import shutil
    shutil.rmtree(cloud_folder, ignore_errors=True)

    # --- 停止按钮：卸掉当前媒体，回到首屏
    window.overlay.bar.stop_button.click()
    settle(app, 0.8)
    expect("停止按钮回到首屏", window.welcome.isVisible())
    expect("停止后媒体已卸载", not window.engine.get("path"), window.engine.get("path"))
    expect("停止后当前媒体清空", window._current is None, window._current)
    expect("停止后 AI 轨与任务都收掉", window.pipeline.ai_pid is None,
           window.pipeline.ai_pid)

    # --- 播放列表：能移除单个文件，且不删磁盘文件
    playlist = window.sidebar.playlist
    playlist.list.clear()
    keep1 = cfg.WORK_DIR / "fixture" / "playlist-remove-a.mp4"
    keep2 = cfg.WORK_DIR / "fixture" / "playlist-remove-b.mp4"
    keep1.write_text("x", encoding="utf-8")
    keep2.write_text("x", encoding="utf-8")
    playlist.append(str(keep1))
    playlist.append(str(keep2))
    playlist.list.setCurrentRow(0)
    playlist.remove_selected()
    expect("播放列表能移除选中的单个文件", playlist.list.count() == 1, playlist.list.count())
    expect("移除只动列表，不删磁盘文件", keep1.exists() and keep2.exists())
    expect("移除后另一项还在", playlist.list.item(0).data(Qt.UserRole) == str(keep2))
    playlist.list.clear()
    playlist.append(str(keep1))
    playlist.list.setCurrentRow(0)
    QApplication.sendEvent(playlist.list, QKeyEvent(QEvent.KeyPress, Qt.Key_Delete, Qt.NoModifier))
    expect("选中后按 Delete 也能移除", playlist.list.count() == 0, playlist.list.count())
    keep1.unlink(missing_ok=True)
    keep2.unlink(missing_ok=True)

    # --- 退出要带走 mpv，不留孤儿进程
    engine_pid = window.engine.proc.pid if window.engine.proc else None
    window.close()
    settle(app, 1.0)
    alive = False
    if engine_pid:
        try:
            os.kill(engine_pid, 0)
            alive = True
        except OSError:
            alive = False
    expect("关窗后 mpv 一起退出，不留孤儿", not alive, f"pid={engine_pid} alive={alive}")

    (cfg.SUPPORT_DIR / "verify_ui.toml").unlink(missing_ok=True)
    print("\nUIACTION PASS" if not FAILURES else f"\nUIACTION FAIL: {FAILURES}")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
