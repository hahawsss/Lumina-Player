"""应用配置：磁盘持久化 + macOS 钥匙串存密钥。

配置写在 ~/Library/Application Support/Lumina/config.toml，可直接手改。
API key 一律不进配置文件，只进钥匙串（或环境变量 LUMINA_OPENAI_KEY / LUMINA_DEEPL_KEY）。
"""
from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
import threading
import tomllib
from pathlib import Path

APP_NAME = "Lumina"
KEYCHAIN_SERVICE = "Lumina Player"

SUPPORT_DIR = Path(os.path.expanduser(f"~/Library/Application Support/{APP_NAME}"))
CONFIG_PATH = SUPPORT_DIR / "config.toml"
MODEL_DIR = SUPPORT_DIR / "models"          # 下载的模型都进这个独立文件夹
ASR_MODEL_DIR = MODEL_DIR / "whisper"       # 转写模型（ggml-*.bin）
LLM_MODEL_DIR = MODEL_DIR / "llm"           # 翻译模型（*.gguf）
WORK_DIR = SUPPORT_DIR / "work"
LIVE_DIR = WORK_DIR / "live"                 # 实时翻译的临时字幕，退出后清掉
SUBTITLE_DIR = SUPPORT_DIR / "subtitles"
CACHE_DIR = SUPPORT_DIR / "cache"
LOG_PATH = SUPPORT_DIR / "lumina.log"        # mpv 的日志（每次启动会被 mpv 覆盖）
APP_LOG_PATH = SUPPORT_DIR / "app.log"       # 应用自己的启动/异常日志
LANGUAGE_HINTS_PATH = CACHE_DIR / "language_hints.json"  # 每部片单独记「识别语言」

MEDIA_EXTS = {
    ".mkv", ".mp4", ".m4v", ".mov", ".avi", ".wmv", ".flv", ".webm", ".ts", ".m2ts",
    ".mts", ".mpg", ".mpeg", ".vob", ".ogv", ".ogm", ".3gp", ".asf", ".rm", ".rmvb",
    ".f4v", ".divx", ".mxf", ".wtv", ".dv", ".nut", ".y4m", ".h264", ".hevc", ".ivf",
    ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".wav", ".aiff", ".aif",
    ".aifc", ".caf", ".au", ".ape", ".wv", ".mka", ".dsf", ".dff", ".ac3", ".m4b",
    ".mp2", ".mpa", ".ra", ".tta", ".spx", ".voc", ".amr", ".3ga", ".oma",
}
SUB_EXTS = {".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx", ".usfrt", ".lrc"}


def media_filter() -> str:
    """文件对话框的媒体过滤串由 MEDIA_EXTS 生成，避免手写漏掉 wmv 这类格式。"""
    patterns = " ".join(f"*{ext}" for ext in sorted(MEDIA_EXTS))
    return f"媒体文件 ({patterns});;所有文件 (*)"


def _empty_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_dirs() -> None:
    for p in (SUPPORT_DIR, MODEL_DIR, ASR_MODEL_DIR, LLM_MODEL_DIR,
              WORK_DIR, LIVE_DIR, SUBTITLE_DIR, CACHE_DIR):
        _empty_dir(p)


def clean_live_subtitles() -> int:
    """实时字幕是临时的：上次异常退出留下的 .ass 在启动时清掉，不进字幕库。"""
    removed = 0
    if LIVE_DIR.is_dir():
        for path in LIVE_DIR.glob("*.ass"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    return removed


_UNSAFE = re.compile(r"[\\/\x00-\x1f]")


def safe_name(text: str, limit: int = 60) -> str:
    """把视频文件名变成能当文件夹/文件名的安全片段。

    斜杠和控制字符一律换掉 —— 片子叫 `../../etc/x` 时不能真跑出根目录去。
    """
    name = _UNSAFE.sub("-", str(text)).strip().strip(". ")
    if len(name) > limit:
        name = name[:limit].rstrip(". ")
    return name or "lumina"


def subtitle_root(value: str | Path | None = None) -> Path:
    """字幕产物的根目录：设置里填了就用填的，空或建不出来就回到软件数据目录。"""
    raw = str(value if value is not None else "").strip()
    if not raw:
        return SUBTITLE_DIR
    try:
        path = Path(raw).expanduser()
        if path.suffix:                 # 用户填成了文件名，取它的父目录
            path = path.parent
        _empty_dir(path)
        resolved = path.resolve()
        return resolved if os.access(resolved, os.W_OK) else SUBTITLE_DIR
    except OSError:
        return SUBTITLE_DIR


def subtitle_root_for(media: str | Path | None, value: str | Path | None = None,
                      next_to_video: bool = False) -> Path:
    """选定这一部片子的字幕根目录：跟随视频优先，视频目录写不进去再回退。"""
    if next_to_video and media:
        try:
            parent = Path(media).expanduser().parent
            if parent.is_dir() and os.access(parent, os.W_OK):
                return parent.resolve()
        except OSError:
            pass
    return subtitle_root(value)


def migrate_legacy_subtitles() -> int:
    """把老版本 work/<片名>/lumina-translated.ass 搬到新目录并改名，只搬不删。"""
    moved = 0
    if not WORK_DIR.is_dir():
        return moved
    for old_dir in WORK_DIR.iterdir():
        if not old_dir.is_dir():
            continue
        legacy = old_dir / "lumina-translated.ass"
        if not legacy.is_file():
            continue
        stem = safe_name(old_dir.name)
        target = subtitle_root() / stem
        dest = target / f"{stem}.双语.ass"
        if dest.exists():
            continue
        try:
            _empty_dir(target)
            legacy.rename(dest)
            moved += 1
        except OSError:
            continue
    return moved


def migrate_legacy_models() -> int:
    """老版本把 ggml-*.bin 直接扔在 models/ 下；新版按类型分 whisper/ 与 llm/。"""
    moved = 0
    if not MODEL_DIR.is_dir():
        return moved
    for src in MODEL_DIR.glob("ggml-*.bin"):
        dest = ASR_MODEL_DIR / src.name
        if dest.exists():
            continue
        try:
            _empty_dir(ASR_MODEL_DIR)
            src.replace(dest)
            moved += 1
        except OSError:
            continue
    return moved


def append_log(text: str) -> None:
    """GUI 应用没有控制台，启动期的异常只能写进文件里才看得见。"""
    try:
        ensure_dirs()
        with APP_LOG_PATH.open("a", encoding="utf-8") as handle:
            import datetime
            handle.write(f"[{datetime.datetime.now():%H:%M:%S}] [app] {text}\n")
    except OSError:
        pass


_LANG_HINTS: dict | None = None
_LANG_LOCK = threading.Lock()


def _lang_key(media: str) -> str:
    return str(Path(media).expanduser()) if media else ""


def _load_lang_hints() -> dict:
    global _LANG_HINTS
    if _LANG_HINTS is None:
        try:
            data = json.loads(LANGUAGE_HINTS_PATH.read_text(encoding="utf-8"))
            _LANG_HINTS = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            _LANG_HINTS = {}
    return _LANG_HINTS


def forget_language_hints() -> None:
    """清理掉每片语言台账后要把内存里的副本也扔掉，否则删了文件还照用。"""
    global _LANG_HINTS
    with _LANG_LOCK:
        _LANG_HINTS = None


def media_language_hint(media: str) -> str:
    """用户给这部片单独指定的识别语言；没指定返回 auto。"""
    key = _lang_key(media)
    if not key:
        return "auto"
    with _LANG_LOCK:
        row = _load_lang_hints().get(key) or {}
    hint = str(row.get("hint") or "auto")
    return hint or "auto"


def set_media_language_hint(media: str, hint: str) -> None:
    key = _lang_key(media)
    if not key:
        return
    with _LANG_LOCK:
        data = _load_lang_hints()
        row = data.get(key) if isinstance(data.get(key), dict) else {}
        row["hint"] = hint or "auto"
        data[key] = row
        try:
            LANGUAGE_HINTS_PATH.parent.mkdir(parents=True, exist_ok=True)
            temp = LANGUAGE_HINTS_PATH.with_suffix(".tmp")
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                            encoding="utf-8")
            temp.replace(LANGUAGE_HINTS_PATH)
        except OSError as exc:
            append_log(f"写每片识别语言失败：{exc}")


DEFAULTS: dict = {
    "player": {
        # libmpv = 画面画进本窗口（真单窗口）；ipc = 起 mpv 子进程 + 独立播放窗
        "backend": "libmpv",
        "libmpv_path": "",
        # auto-safe = 优先硬解、失败自动回软解；videotoolbox = 强制硬解；no = 强制软解
        "hwdec": "auto-safe",
        # 渲染后端：mpv 0.41 的 Homebrew 构建没有 cocoa GL 上下文，只有 Vulkan(macvk)，
        # 而 --wid 内嵌依赖 GL/cocoa-cb —— 空值表示让 mpv 自己选。
        "vo": "gpu",
        "gpu_context": "",
        # 留空 = 自动挑（brew 的 /opt/homebrew/bin/mpv，其次 PATH 上的）
        "mpv_path": "",
        "volume": 100.0,
        "speed": 1.0,
        "audio_passthrough": False,
        "embed_mpv": False,        # 仅 ipc 后端：当前 mpv 在 macOS 上不支持 --wid 内嵌，见 engine.can_embed
        "pin_window": True,        # 仅 ipc 后端：内嵌不可用时把 mpv 播放窗贴成我们视频区的大小
    },
    "languages": {
        # 默认音轨/字幕语言偏好，按顺序优先匹配；写法用 BCP-47 基础子标签即可
        "default_audio_langs": ["zho", "chi", "zh", "en"],
        "default_subtitle_langs": ["zho", "chi", "zh", "eng", "en"],
        # original | translated | bilingual
        "subtitle_display": "bilingual",
        "translation_target": "zh-CN",
    },
    "subtitle": {
        "font": "PingFang SC",
        # size/outline/shadow 用 mpv 与 ASS 都成立的像素语义（size 对 720 高的画面）
        "size": 50,
        "outline": 3,
        "shadow": 2,
        "margin": 42,           # 我们生成的双语字幕距底边（PlayResY=720 坐标系）
        "pos": 92,              # 原生字幕的 mpv sub-pos：100 会贴着底边压在控制条下面
        "delay": 0.0,
        "bold": True,
        # AI/翻译字幕相对原文的位置：below | above
        "translated_position": "below",
        # 生成字幕的根目录，空 = ~/Library/Application Support/Lumina/subtitles
        "output_dir": "",
        # True = 每部片子的字幕放视频所在目录（写不进去时回退到 output_dir / 软件数据目录）
        "next_to_video": False,
    },
    "translate": {
        # openai | deepl | argos | llama（本地 llama.cpp + GGUF，离线）
        "engine": "openai",
        "openai_base_url": "https://api.openai.com/v1",
        "openai_model": "gpt-4o-mini",
        "deepl_free": True,
        "deepl_target": "ZH",
        # 本地引擎：二进制留空 = 自动找 brew 的 llama-server；模型是 models/llm/ 下的文件名
        "llama_binary": "",
        "llama_model": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "live_llama_model": "qwen2.5-0.5b-instruct-q4_k_m.gguf",
        "models_split_v1": False,
        "batch_lines": 24,
        "live_batch_lines": 8,   # 实时翻译一小批一小批翻，译文能更快跟上画面
        "context_lines": 0,      # >0 = 保留前 N 句原文/译文给 LLM 当上下文；0 关闭
        "cache": True,
        "use_system_proxy": True,
    },
    "asr": {
        "binary": "whisper-cli",
        "model": "ggml-small-q5_1.bin",
        "language": "auto",
        "window": 20.0,       # 实时流窗口长度（秒）
        "overlap": 4.0,       # 窗口重叠，用来接住被切开的一句话
        "live_lead": 30.0,    # 实时翻译预读：尽量把字幕/翻译做到播放头前方这么多秒
        "max_chars": 44,      # 单条字幕上限，超长按标点+时长比例再切
        "threads": 0,         # 0 = 交给 whisper 自己决定
    },
    "cloud": {
        "openlist_port": 15244,
        "openlist_binary": "",
        # auto = 打开软件就把网关带起来、关窗收掉；manual = 只在网盘管理页手动启停
        "start_mode": "auto",
        # 实时翻译走本机单流代理：内存缓存上限 + 云端预读上限
        "live_cache_mb": 128,
        "live_lead_max": 60,
    },
    "ui": {
        "theme": "dark",
        "accent": "#4fa8ff",
        "show_chapter_bar": True,
    },
    "recent": [],
}

DEEP_TARGET_MAP = {
    "zh-CN": "ZH", "zh-TW": "ZH", "en": "EN", "ja": "JA", "ko": "KO", "fr": "FR",
    "de": "DE", "es": "ES", "ru": "RU", "it": "IT", "pt": "PT-BR", "pl": "PL",
    "tr": "TR", "id": "ID", "uk": "UK", "nl": "NL", "sv": "SV",
}

LANGUAGE_CHOICES = [
    ("zh-CN", "简体中文"),
    ("zh-TW", "繁體中文"),
    ("en", "English"),
    ("ja", "日本語"),
    ("ko", "한국어"),
    ("fr", "Français"),
    ("de", "Deutsch"),
    ("es", "Español"),
    ("ru", "Русский"),
    ("it", "Italiano"),
    ("pt", "Português"),
    ("th", "ไทย"),
    ("vi", "Tiếng Việt"),
    ("id", "Bahasa Indonesia"),
    ("ar", "العربية"),
    ("pl", "Polski"),
    ("nl", "Nederlands"),
    ("tr", "Türkçe"),
    ("uk", "Українська"),
]

LANGUAGE_DISPLAY = {
    "zh": "中文", "zho": "中文", "chi": "中文", "cmn": "中文", "chs": "中文", "cht": "中文",
    "zh-cn": "简体中文", "zh-tw": "繁體中文", "en": "英文", "eng": "英文",
    "ja": "日文", "jpn": "日文", "ko": "韩文", "kor": "韩文", "fr": "法文", "fra": "法文",
    "fre": "法文", "de": "德文", "deu": "德文", "ger": "德文", "es": "西文", "spa": "西文",
    "ru": "俄文", "rus": "俄文", "it": "意文", "ita": "意文", "pt": "葡文", "por": "葡文",
    "th": "泰文", "tha": "泰文", "vi": "越文", "vie": "越文", "id": "印尼文", "ind": "印尼文",
    "ar": "阿拉伯文", "ara": "阿拉伯文", "pl": "波兰文", "pol": "波兰文", "nl": "荷兰文",
    "dut": "荷兰文", "nld": "荷兰文", "tr": "土耳其文", "tur": "土耳其文", "uk": "乌克兰文",
    "ukr": "乌克兰文", "und": "未标注", "---": "无语言", "": "无语言",
}

_HWDEC_ALIASES = {
    "自动（优先硬解）": "auto-safe",
    "强制硬解（VideoToolbox）": "videotoolbox",
    "强制硬解（拷贝帧）": "videotoolbox-copy",
    "强制软解": "no",
    "全自动": "auto",
}


def hwdec_label(mode: str) -> str:
    for label, value in _HWDEC_ALIASES.items():
        if value == mode:
            return label
    return mode


def hwdec_mode_for_label(label: str) -> str:
    return _HWDEC_ALIASES.get(label, label)


def hwdec_choices() -> list[str]:
    return list(_HWDEC_ALIASES.keys())


def lang_display(tag: str | None) -> str:
    if not tag:
        return "未标注"
    key = tag.strip().lower()
    if key in LANGUAGE_DISPLAY:
        return LANGUAGE_DISPLAY[key]
    base = key.split("-")[0]
    if base in LANGUAGE_DISPLAY:
        suffix = key[len(base):]
        return LANGUAGE_DISPLAY[base] + (suffix.upper() if suffix else "")
    return tag


# ---------------------------------------------------------------- TOML 读写

def _scalar_to_toml(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    raise TypeError(f"不支持的配置值类型: {type(value)}")


def dump_toml(data: dict) -> str:
    """只覆盖「两层表 + 标量/字符串列表」这一子集，够用且可手改。"""
    out: list[str] = []
    scalars = {k: v for k, v in data.items() if not isinstance(v, dict)}
    for key, value in scalars.items():
        if isinstance(value, list):
            items = ", ".join(_scalar_to_toml(v) for v in value)
            out.append(f"{key} = [{items}]")
        else:
            out.append(f"{key} = {_scalar_to_toml(value)}")
    for section, table in data.items():
        if not isinstance(table, dict):
            continue
        if out:
            out.append("")
        out.append(f"[{section}]")
        for key, value in table.items():
            if isinstance(value, list):
                items = ", ".join(_scalar_to_toml(v) for v in value)
                out.append(f"{key} = [{items}]")
            else:
                out.append(f"{key} = {_scalar_to_toml(value)}")
    return "\n".join(out) + "\n"


def _merge(base: dict, incoming: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in incoming.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


class Config:
    def __init__(self, path: Path = CONFIG_PATH):
        self.path = path
        self.data = copy.deepcopy(DEFAULTS)
        self._listeners: list = []
        self.load()

    def load(self) -> None:
        if self.path.exists():
            try:
                raw = tomllib.loads(self.path.read_text(encoding="utf-8"))
                self.data = _merge(DEFAULTS, raw)
            except Exception as exc:  # 配置文件坏了也不能让播放器打不开
                print(f"[config] 解析 {self.path} 失败，用默认值: {exc}", file=sys.stderr)
                self.data = copy.deepcopy(DEFAULTS)
        self._apply_env_overrides()

    def _apply_env_overrides(self) -> None:
        if os.environ.get("LUMINA_HWDEC"):
            self.data["player"]["hwdec"] = os.environ["LUMINA_HWDEC"]
        if os.environ.get("LUMINA_EMBED"):
            self.data["player"]["embed_mpv"] = os.environ["LUMINA_EMBED"] != "0"

    def save(self) -> None:
        ensure_dirs()
        tmp = self.path.with_suffix(".toml.tmp")
        tmp.write_text(dump_toml(self.data), encoding="utf-8")
        tmp.replace(self.path)

    def get(self, dotted: str, default=None):
        node = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value, save: bool = True) -> None:
        section, _, key = dotted.partition(".")
        if not key:
            self.data[section] = value
        else:
            self.data.setdefault(section, {})[key] = value
        if save:
            self.save()
        for listener in list(self._listeners):
            listener(dotted, value)

    def on_change(self, listener) -> None:
        self._listeners.append(listener)

    @property
    def recent(self) -> list[str]:
        return list(self.data.get("recent") or [])

    def push_recent(self, path: str, limit: int = 24) -> None:
        items = [p for p in self.recent if p != path]
        items.insert(0, path)
        self.data["recent"] = items[:limit]
        self.save()


# ---------------------------------------------------------------- 钥匙串

def secret_get(name: str) -> str | None:
    env_key = "LUMINA_" + name.upper().replace("-", "_") + "_KEY"
    from_env = os.environ.get(env_key)
    if from_env:
        return from_env
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", name, "-w"],
            capture_output=True, text=True,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    return value or None


def secret_set(name: str, value: str) -> None:
    args = ["security", "add-generic-password", "-U", "-s", KEYCHAIN_SERVICE, "-a", name, "-w", value]
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"写入钥匙串失败: {proc.stderr.strip()}")


def secret_delete(name: str) -> None:
    subprocess.run(
        ["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", name],
        capture_output=True,
    )
