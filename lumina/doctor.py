"""环境体检与自愈：缺 mpv / ffmpeg / whisper 时直接在应用里装，不用回终端。

只依赖 Homebrew 存在；安装过程流式读 brew 输出，可取消。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from . import asr, config as cfg, engine as mpv_engine, i18n, translate


@dataclass
class Item:
    key: str
    label: str
    ok: bool
    detail: str
    formula: str = ""          # 空 = 没法自动装
    required: bool = True      # False = 缺了只是少个功能

    @property
    def installable(self) -> bool:
        return bool(self.formula) and not self.ok


def brew_path() -> str:
    for candidate in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew"):
        if os.path.isfile(candidate):
            return candidate
    return shutil.which("brew") or ""


def find_tool(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    for prefix in ("/opt/homebrew/bin", "/usr/local/bin"):
        path = os.path.join(prefix, name)
        if os.path.isfile(path):
            return path
    return ""


def check(config: cfg.Config) -> list[Item]:
    items: list[Item] = []
    backend = str(config.get("player.backend", "libmpv") or "libmpv")
    if backend == "libmpv":
        # 真内嵌用的是 libmpv.2.dylib，不是 mpv 可执行文件——体检要对着实际依赖说
        from . import mpv_lib
        try:
            path = mpv_lib.libmpv_path(config)
            items.append(Item("mpv", i18n.t("播放引擎 libmpv"), True, Path(path).name))
        except Exception as exc:
            items.append(Item("mpv", i18n.t("播放引擎 libmpv"), False, str(exc), "mpv"))
        items.append(Item("embed", i18n.t("画面呈现方式"), True, i18n.t("画进本窗口（GL 内嵌）"), "",
                          required=False))
        return _check_rest(items, config)

    try:
        items.append(Item("mpv", i18n.t("播放引擎 mpv"), True, mpv_engine.find_mpv_binary()))
    except mpv_engine.MpvError as exc:
        items.append(Item("mpv", i18n.t("播放引擎 mpv"), False, str(exc), "mpv"))

    binary = mpv_engine.find_mpv_binary(config)
    embeddable = mpv_engine.can_embed(binary)
    pinned = bool(config.get("player.pin_window", True))
    items.append(Item(
        "embed", i18n.t("画面呈现方式"), embeddable or pinned,
        i18n.t("内嵌进本窗口") if embeddable else
        (Path(binary).name + i18n.t(" · 独立播放窗，已贴合视频区" if pinned
                                    else " · 独立播放窗，未贴合")),
        "", required=False))
    return _check_rest(items, config)


def _whisper_installed(config: cfg.Config) -> bool:
    """whisper 不可用分成「没装」和「缺模型」：只有前者能用 brew 补。"""
    try:
        asr.find_binary(str(config.get("asr.binary", "whisper-cli")))
        return True
    except asr.AsrError:
        return False


def _check_rest(items: list[Item], config: cfg.Config) -> list[Item]:
    for name, label in (("ffmpeg", "音频抽取 ffmpeg"), ("ffprobe", "流探测 ffprobe")):
        found = find_tool(name)
        items.append(Item(name, i18n.t(label), bool(found), found or i18n.t("未找到"), "ffmpeg"))

    try:
        ok, message = asr.availability_cached(config)
        items.append(Item("whisper", i18n.t("语音转字幕 whisper.cpp"), ok, message,
                          "" if _whisper_installed(config) else "whisper-cpp",
                          required=False))
    except asr.AsrError as exc:
        items.append(Item("whisper", i18n.t("语音转字幕 whisper.cpp"), False, str(exc),
                          "whisper-cpp", required=False))

    models = asr.list_models()
    items.append(Item("model", i18n.t("转写模型"), bool(models),
                      ", ".join(models) if models else i18n.t("未下载（AI 面板里可下载）"),
                      "", required=False))

    binary = translate.find_llama_binary(config)
    llm_model = str(config.get("translate.llama_model", "") or "")
    ready = translate.llm_model_ready(llm_model)
    if binary and ready:
        detail = i18n.t("llama-server 就绪 · {}", llm_model)
    elif binary:
        detail = i18n.t("已装 llama.cpp，缺模型 {}（AI 面板里点下载）", llm_model)
    else:
        detail = i18n.t("未安装：离线翻译不可用（brew install llama.cpp）")
    items.append(Item("llama", i18n.t("本地翻译 llama.cpp"), bool(binary) and ready, detail,
                      "" if binary else "llama.cpp", required=False))

    has_key = bool(cfg.secret_get("openai") or cfg.secret_get("deepl"))
    items.append(Item("key", i18n.t("翻译密钥"), has_key,
                      i18n.t("已配置") if has_key else
                      i18n.t("未配置：翻译不可用（设置里填，或环境变量 LUMINA_OPENAI_KEY）"),
                      "", required=False))
    return items


def summarize(items: list[Item]) -> str:
    sep = "、" if i18n.current() == "zh" else ", "
    blocked = [item.label for item in items if item.required and not item.ok]
    if blocked:
        return i18n.t("缺少必需组件：{}", sep.join(blocked))
    optional = [item.label for item in items if not item.required and not item.ok]
    if optional:
        return i18n.t("可以播放；这些功能待补：{}", sep.join(optional))
    return i18n.t("环境完整，全部功能可用")


class Installer(QObject):
    """brew install 的后台执行：逐行回显，可取消。"""

    line = Signal(str)
    done = Signal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()

    def formulas(self, items: list[Item]) -> list[str]:
        out: list[str] = []
        for item in items:
            if item.installable and item.formula not in out:
                out.append(item.formula)
        return out

    def start(self, items: list[Item]) -> bool:
        formulas = self.formulas(items)
        brew = brew_path()
        if not formulas:
            self.done.emit(True, i18n.t("没有需要安装的组件"))
            return False
        if not brew:
            self.done.emit(False, i18n.t("没找到 Homebrew，无法自动安装。先装 brew：https://brew.sh"))
            return False
        if self.running():
            return False
        self._cancel.clear()

        def worker() -> None:
            cmd = [brew, "install", *formulas]
            self.line.emit("$ " + " ".join(cmd))
            env = dict(os.environ)
            env["HOMEBREW_NO_AUTO_UPDATE"] = "1"
            env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + env.get("PATH", "")
            try:
                self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                              stderr=subprocess.STDOUT, text=True,
                                              env=env, bufsize=1)
            except OSError as exc:
                self.done.emit(False, i18n.t("启动 brew 失败：{}", exc))
                return
            tail: list[str] = []
            assert self._proc.stdout is not None
            for raw in self._proc.stdout:
                if self._cancel.is_set():
                    self._proc.terminate()
                    break
                text = raw.rstrip()
                if text:
                    tail.append(text)
                    del tail[:-40]
                    self.line.emit(text)
            code = self._proc.wait()
            if self._cancel.is_set():
                self.done.emit(False, i18n.t("已取消安装"))
            elif code == 0:
                self.done.emit(True, i18n.t("安装完成：{}", ", ".join(formulas)))
            else:
                self.done.emit(False, i18n.t("brew 退出码 {}：{}", code,
                                             " / ".join(tail[-3:])))

        self._thread = threading.Thread(target=worker, daemon=True, name="brew-install")
        self._thread.start()
        return True

    def cancel(self) -> None:
        self._cancel.set()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())
