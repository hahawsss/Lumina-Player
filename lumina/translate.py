"""翻译后端：OpenAI 兼容接口 / DeepL / 本地 Argos，共用一份磁盘缓存。

在线接口走 httpx，会自动采用 macOS 系统代理（本机 huggingface、OpenAI 都需要代理）。
密钥只从钥匙串/环境变量取，不落到配置文件。
"""
from __future__ import annotations

import atexit
import hashlib
from difflib import SequenceMatcher
import json
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import httpx

from . import config as cfg
from .langtags import base_of


class TranslationError(RuntimeError):
    pass


# 提示词改过一轮：旧缓存里可能存着“把日文原样当译文”的坏结果，换版本号全部跳过
PROMPT_VERSION = "2026-10-26-2"
_KANA_TEXT = re.compile(r"[\u3040-\u30ff]")
_HAN_TEXT = re.compile(r"[\u3400-\u9fff]")
_HANGUL_TEXT = re.compile(r"[\uac00-\ud7af]")


def looks_untranslated(source_text: str, out_text: str, source_lang: str | None,
                       target: str | None) -> bool:
    """模型把原文原样返回，尤其是日译中时，不能当成翻译成功。"""
    if not (out_text or "").strip():
        return True
    norm = lambda value: re.sub(r"[\s，。！？；：、,.!?;:…]+", "", value).lower()
    src, tgt = base_of(source_lang), base_of(target)
    same = norm(source_text) == norm(out_text)
    if tgt:
        # 目标是中文/英文等，译文里出现假名/韩文基本就是原文没翻
        if tgt != "ja" and _KANA_TEXT.search(out_text or ""):
            return True
        if tgt != "ko" and _HANGUL_TEXT.search(out_text or ""):
            return True
    if not same:
        if (src and tgt and src == tgt) or src not in ("ja", "zh", "ko"):
            return False
        a, b = norm(source_text), norm(out_text)
        if a in b or b in a:
            longer, shorter = (a, b) if len(a) >= len(b) else (b, a)
            extra = longer.replace(shorter, "", 1)
            if extra and len(extra) <= 6 and re.fullmatch(
                    r"[\u3040-\u30ff\s、。…]+", extra):
                return True          # 只多了日语助词，仍是原文
        if (len(a) >= 6 and len(b) >= 6 and a[0] == b[0] and a[-1] == b[-1]
                and SequenceMatcher(None, a, b).ratio() >= 0.82):
            return True              # 只改了个别助词/标点的“翻译”仍是原文
        return False
    if src and tgt and src == tgt:
        return False                 # 本来就同语言，原样是正常的
    if _KANA_TEXT.search(source_text or ""):
        return True                  # 带假名还和原文一样，日译中肯定没翻
    if src == "zh" and tgt not in ("zh", "ja") and _HAN_TEXT.search(source_text or ""):
        return True
    if src == "ko" and tgt != "ko" and _HANGUL_TEXT.search(source_text or ""):
        return True
    return False


@dataclass
class Probe:
    ok: bool
    detail: str
    latency_ms: float = 0.0


def system_proxy() -> dict[str, str]:
    """macOS 的系统代理设置在 Environment 变量里通常为空，要靠 scutil 解析。"""
    try:
        found = urllib.request.getproxies()
    except Exception:
        found = {}
    out = {}
    for scheme in ("http", "https"):
        value = found.get(scheme)
        if value:
            out[scheme] = value
    return out


def http_client(timeout, url: str = "") -> httpx.Client:
    """httpx 0.28 起 `proxies` 参数被删了，只认 `proxy` —— 两个名字都试一次，
    免得又出现「装得上、一下载就炸」。"""
    # 本地网关（Ollama / LM Studio / llama-server）不能被系统代理劫走
    proxies = {} if _is_loopback(url) else system_proxy()
    kwargs = dict(timeout=timeout, follow_redirects=True)
    if proxies:
        try:
            return httpx.Client(proxies=proxies, **kwargs)
        except TypeError:
            return httpx.Client(proxy=proxies.get("https"), **kwargs)
    return httpx.Client(trust_env=False, **kwargs)


def _is_loopback(url: str) -> bool:
    host = (url.split("://")[-1].split("/")[0] if "://" in url else url).split(":")[0]
    return host in ("localhost", "127.0.0.1", "::1", "0.0.0.0", "")


class _DownloadCancelled(Exception):
    pass


def download_from_mirrors(urls, dest: Path, on_progress=None, cancel=None, *,
                          error_cls=TranslationError, on_note=None,
                          retries: int = 3, chunk: int = 1 << 20) -> Path:
    """镜像 + 断点续传 + 短读超时重试：网络断了保留 .part，下次从原进度接着下。"""
    dest = Path(dest)
    part = dest.with_name(dest.name + ".part")
    errors: list[str] = []
    for attempt in range(max(1, retries)):
        for url in urls:
            if cancel is not None and cancel.is_set():
                raise error_cls("已取消下载")
            try:
                _download_one(url, part, dest, on_progress, cancel, on_note, chunk)
                return dest
            except _DownloadCancelled:
                raise error_cls("已取消下载")
            except Exception as exc:
                host = url.split("/")[2] if "://" in url else url
                errors.append(f"{host}: {exc}")
                if on_note:
                    on_note(f"{host} 断了（{exc}），自动重试…")
                if cancel is not None and cancel.is_set():
                    raise error_cls("已取消下载")
                time.sleep(1.0 + attempt)
    raise error_cls("模型下载失败（已保留断点，再点一次会接着下）：\n"
                    + "\n".join(errors[-6:]))


def _download_one(url: str, part: Path, dest: Path, on_progress, cancel, on_note,
                  chunk: int) -> None:
    offset = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    timeout = httpx.Timeout(20.0, connect=20.0, read=30.0, write=30.0, pool=20.0)
    with http_client(timeout, url) as client:
        with client.stream("GET", url, headers=headers) as reply:
            if offset and reply.status_code == 416:
                part.unlink(missing_ok=True)
                raise TranslationError("断点已失效，重新下载")
            reply.raise_for_status()
            if offset and reply.status_code != 206:
                offset = 0
                mode = "wb"
            else:
                mode = "ab" if offset else "wb"
            if on_note:
                on_note(f"从 {offset / 1e6:.1f} MB 处继续下载…" if offset else "开始下载…")
            remaining = int(reply.headers.get("content-length") or 0)
            # 206 的绝对总长以 Content-Range 为准；只拿 content-length 会在
            # 某些镜像上把「剩余长度」和 offset 加错，进度就会超过 100%
            total = 0
            content_range = reply.headers.get("content-range") or ""
            if "/" in content_range:
                try:
                    total = int(content_range.rsplit("/", 1)[1])
                except ValueError:
                    total = 0
            if total <= 0:
                total = offset + remaining if remaining else 0
            if total and offset >= total:
                part.unlink(missing_ok=True)
                raise TranslationError("断点比文件还大，重新下载")
            done = offset
            with part.open(mode) as handle:
                for data in reply.iter_bytes(chunk):
                    if cancel is not None and cancel.is_set():
                        raise _DownloadCancelled
                    handle.write(data)
                    done += len(data)
                    if on_progress and total:
                        on_progress(done, total)
            size = part.stat().st_size
            if total and size != total:
                raise TranslationError(
                    f"下载长度不对（{size / 1e6:.1f} / {total / 1e6:.1f} MB），保留断点重试")
    part.replace(dest)
    if on_note:
        on_note("下载完成")


class Cache:
    """追加式 JSONL：读一次进内存，之后每条译文落一行，崩溃也不丢已完成的。"""

    def __init__(self, path=None, enabled: bool = True):
        self.path = path or (cfg.CACHE_DIR / "translations.jsonl")
        self.enabled = enabled
        self._lock = threading.Lock()
        self._store: dict[str, str] = {}
        self._loaded = False
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(engine: str, model: str, target: str, text: str) -> str:
        norm = re.sub(r"\s+", " ", text).strip().lower()
        raw = f"{engine}|{model}|{target}|{norm}".encode("utf-8")
        return hashlib.sha1(raw).hexdigest()

    def load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and "k" in row and "v" in row:
                    self._store[row["k"]] = row["v"]

    def get(self, key: str) -> str | None:
        if not self.enabled:
            return None
        with self._lock:
            self.load()
            value = self._store.get(key)
            if value is None:
                self.misses += 1
            else:
                self.hits += 1
            return value

    def put(self, key: str, value: str) -> None:
        if not self.enabled or not value:
            return
        with self._lock:
            self.load()
            if self._store.get(key) == value:
                return
            self._store[key] = value
            cfg.ensure_dirs()
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"k": key, "v": value}, ensure_ascii=False) + "\n")

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._loaded = True
            if self.path.exists():
                self.path.unlink(missing_ok=True)


class Translator:
    name = "base"
    model_label = ""
    supports_line_breaks = True

    def translate(self, texts: list[str], target: str, source: str | None = None,
                  context: str | None = None) -> list[str]:
        raise NotImplementedError

    def probe(self, target: str) -> Probe:
        started = time.time()
        try:
            out = self.translate(["The quick brown fox jumps over the lazy dog."], target)
            text = out[0] if out else ""
            latency = (time.time() - started) * 1000
            if not text.strip():
                return Probe(False, "返回了空结果", latency)
            return Probe(True, f"{latency:.0f} ms · {text[:40]}", latency)
        except Exception as exc:
            return Probe(False, f"{type(exc).__name__}: {exc}", (time.time() - started) * 1000)


class OpenAICompat(Translator):
    name = "openai"
    default_timeout = 40.0      # 本地模型子类会放大（推理慢得多）

    def __init__(self, base_url: str, api_key: str, model: str, context_lines: int = 0):
        if not api_key:
            raise TranslationError("没有 OpenAI 兼容接口的密钥（设置里填，或用环境变量 LUMINA_OPENAI_KEY）")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.model_label = model
        self.context_lines = max(0, int(context_lines))
        self.chat_template_kwargs: dict | None = None
        self._context: list[tuple[str, str]] = []
        self._context_lock = threading.Lock()

    def _chat(self, messages, timeout: float | None = None) -> str:
        timeout = timeout or self.default_timeout
        url = f"{self.base_url}/chat/completions"
        body = {"model": self.model, "messages": messages, "temperature": 0.0}
        if self.chat_template_kwargs:
            body["chat_template_kwargs"] = self.chat_template_kwargs
        with http_client(timeout, url) as client:
            reply = client.post(url, json=body, headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            })
            if (reply.status_code == 400 and "chat_template_kwargs" in body
                    and "chat_template" in reply.text):
                # 老 llama-server 不认请求级模板参数；去掉再试一次，别让整批翻译失败
                body.pop("chat_template_kwargs", None)
                reply = client.post(url, json=body, headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                })
        if reply.status_code >= 400:
            detail = reply.text[:400]
            raise TranslationError(f"HTTP {reply.status_code}: {detail}")
        data = reply.json()
        choices = data.get("choices") or []
        if not choices:
            raise TranslationError(f"接口没有返回 choices: {json.dumps(data)[:200]}")
        return (choices[0].get("message") or {}).get("content", "")

    def _context_prefix(self) -> str:
        if self.context_lines <= 0:
            return ""
        with self._context_lock:
            pairs = list(self._context[-self.context_lines:])
        if not pairs:
            return ""
        rows = "\n".join(f"- {src} => {dst}" for src, dst in pairs)
        return f"前文（仅供理解，不要翻译，也不要输出）：\n{rows}\n\n"

    def _remember_context(self, pairs) -> None:
        if self.context_lines <= 0:
            return
        with self._context_lock:
            self._context.extend((src, dst) for src, dst in pairs
                                 if src.strip() and dst.strip())
            self._context = self._context[-self.context_lines:]

    def clear_context(self) -> None:
        with self._context_lock:
            self._context.clear()

    def translate(self, texts, target, source=None, context=None):
        joined = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
        target_name = cfg.lang_display(target)
        source_name = cfg.lang_display(source) if source else ""
        system = (
            f"你是影视字幕译者。把下面带编号的字幕逐行翻译成{target_name}。"
            + (f"源语言是{source_name}。" if source_name else "")
            + "要求：每行只输出译文，不要编号、不要解释、不要合并或拆分行，"
            "不要保留原文。人名、术语、语气保持自然；"
            "原文内部有换行时，译文保持相同换行数。"
        )
        prefix = self._context_prefix()
        if context:
            prefix += f"上下文参考（仅帮助理解，不翻译）：\n{context}\n\n"
        user = prefix + f"输入：\n{joined}"
        raw = self._chat([{"role": "system", "content": system},
                          {"role": "user", "content": user}])
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        cleaned: list[str] = []
        for line in lines:
            stripped = re.sub(r"^\s*\d+[.、)]\s*", "", line)
            if stripped:
                cleaned.append(stripped)
        if len(cleaned) == len(texts):
            self._remember_context(list(zip(texts, cleaned)))
            return cleaned
        # 行数对不上（模型合并了行）→ 退化成逐条请求，保证不错位
        out = []
        for text in texts:
            one = self.translate_one(text, target, context)
            out.append(one)
        self._remember_context(list(zip(texts, out)))
        return out

    def translate_one(self, text: str, target: str, context: str | None = None) -> str:
        # 与批量路径同一套「编号行」协议：模型更不容易加解释，回来也好剥前缀
        target_name = cfg.lang_display(target)
        system = (f"你是影视字幕译者。把编号为 1 的那行字幕翻译成{target_name}，"
                  "只输出该行的译文，不要编号、不要解释、不要保留原文。")
        prefix = self._context_prefix()
        if context:
            prefix += f"上下文参考（仅帮助理解，不翻译）：\n{context}\n\n"
        raw = self._chat([{"role": "system", "content": system},
                          {"role": "user", "content": f"{prefix}1. {text}"}])
        line = raw.strip().splitlines()[0] if raw.strip() else ""
        return re.sub(r"^\s*\d+[.、)]\s*", "", line).strip() or text


class DeepL(Translator):
    name = "deepl"
    model_label = "deepl"

    def __init__(self, api_key: str, free: bool = True):
        if not api_key:
            raise TranslationError("没有 DeepL 密钥（设置里填，或用环境变量 LUMINA_DEEPL_KEY）")
        self.api_key = api_key
        host = "api-free.deepl.com" if free else "api.deepl.com"
        self.url = f"https://{host}/v2/translate"

    def translate(self, texts, target, source=None, context=None):
        from .config import DEEP_TARGET_MAP
        target_code = DEEP_TARGET_MAP.get(target, base_of(target).upper())
        body = {"text": texts, "target_lang": target_code}
        if source:
            body["source_lang"] = base_of(source).upper()
        with http_client(30.0, self.url) as client:
            reply = client.post(self.url, json=body, headers={
                "Authorization": f"DeepL-Auth-Key {self.api_key}",
                "Content-Type": "application/json",
            })
        if reply.status_code >= 400:
            raise TranslationError(f"HTTP {reply.status_code}: {reply.text[:300]}")
        rows = reply.json().get("translations") or []
        out = [row.get("text", "") for row in rows]
        if len(out) != len(texts):
            raise TranslationError(f"DeepL 返回 {len(out)} 条，期望 {len(texts)} 条")
        return out


class Argos(Translator):
    """本地离线引擎（argostranslate + CTranslate2），不需要网络。"""
    name = "argos"
    model_label = "argos"
    supports_line_breaks = False

    def __init__(self, auto_install_hint: bool = True):
        self._module = None
        self.auto_install_hint = auto_install_hint

    def _load(self):
        if self._module is not None:
            return self._module
        try:
            import argostranslate.package
            import argostranslate.translate
        except ImportError as exc:
            raise TranslationError(
                "本地引擎未安装：uv sync --extra offline  (装 argostranslate)") from exc
        self._module = (argostranslate.package, argostranslate.translate)
        return self._module

    def _ensure_package(self, from_code: str, to_code: str) -> None:
        package_module, translate_module = self._load()
        installed = {(p.from_code, p.to_code) for p in translate_module.get_installed_languages()
                    and package_module.get_installed_packages()}
        if (from_code, to_code) in installed:
            return
        available = [p for p in package_module.get_available_packages()
                     if p.from_code == from_code and p.to_code == to_code]
        if not available:
            raise TranslationError(f"Argos 没有 {from_code}→{to_code} 语言包")
        available[0].download()

    def translate(self, texts, target, source=None, context=None):
        _, translate_module = self._load()
        to_code = base_of(target) or "en"
        from_code = base_of(source) or "en"
        self._ensure_package(from_code, to_code)
        out = []
        for text in texts:
            lines = text.split("\n")
            rendered = [translate_module.translate(line, from_code, to_code) for line in lines]
            out.append("\n".join(rendered))
        return out


LLM_MODEL_DIR = cfg.LLM_MODEL_DIR

# 文件名 -> (显示名, 体积与速度, HF 上的路径)。体积是照 HF 的 tree API 核对过的，别凭印象改。
LLM_MODELS = {
    "qwen2.5-0.5b-instruct-q4_k_m.gguf": (
        "Qwen2.5-0.5B · Q4_K_M", "0.46 GB · 最快，但长句可能原样不翻",
        "Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf"),
    "qwen2.5-1.5b-instruct-q4_k_m.gguf": (
        "Qwen2.5-1.5B · Q4_K_M", "1.04 GB · 速度与质量折中",
        "Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf"),
    "qwen2.5-3b-instruct-q4_k_m.gguf": (
        "Qwen2.5-3B · Q4_K_M", "1.96 GB · 质量优先，适合一键翻译",
        "Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf"),
    "qwen2.5-7b-instruct-q4_k_m.gguf": (
        "Qwen2.5-7B · Q4_K_M", "4.7 GB · 质量更高，整片慢慢翻",
        "bartowski/Qwen2.5-7B-Instruct-GGUF/resolve/main/Qwen2.5-7B-Instruct-Q4_K_M.gguf"),
    "qwen3.5-9b-instruct-q4_k_m.gguf": (
        "Qwen3.5-9B · Q4_K_M", "5.2 GB · 新一代，质量优先，整片慢慢翻",
        "lmstudio-community/Qwen3.5-9B-GGUF/resolve/main/Qwen3.5-9B-Q4_K_M.gguf"),
    "qwen2.5-14b-instruct-q4_k_m.gguf": (
        "Qwen2.5-14B · Q4_K_M", "9.0 GB · 最高质量，很慢，24 GB 内存可跑",
        "bartowski/Qwen2.5-14B-Instruct-GGUF/resolve/main/Qwen2.5-14B-Instruct-Q4_K_M.gguf"),
}
_LLM_URLS = ("https://hf-mirror.com/{path}", "https://huggingface.co/{path}")


def llm_model_path(name: str) -> Path:
    return LLM_MODEL_DIR / (name or "")


def llm_model_ready(name: str) -> bool:
    path = llm_model_path(name)
    return path.is_file() and path.stat().st_size > 10_000_000


def list_llm_models() -> list[str]:
    return sorted(p.name for p in LLM_MODEL_DIR.glob("*.gguf")) if LLM_MODEL_DIR.is_dir() else []


def model_problem(config: cfg.Config, live: bool = False) -> str:
    """本地 GGUF 翻译能不能开工；返回人话说明，空串 = 没问题。

    实时槽被选到一个只下了半截的模型时，任务会「看起来在跑但其实没翻」——
    与其让它在后台反复失败，不如在点按钮的那一刻就说清楚。
    """
    if str(config.get("translate.engine", "openai")) != "llama":
        return ""
    key = "translate.live_llama_model" if live else "translate.llama_model"
    name = str(config.get(key, "") or "")
    label = "实时翻译" if live else "整片/一键翻译"
    if not name:
        return f"没选{label}模型"
    if not find_llama_binary(config):
        return "没找到 llama-server：brew install llama.cpp 后重启播放器"
    if llm_model_ready(name):
        return ""
    path = llm_model_path(name)
    part = path.with_name(path.name + ".part")
    try:
        if part.is_file():
            got = part.stat().st_size / 1e6
            return (f"{label}模型 {name} 只下了一半（{got:.0f} MB 断点）："
                    f"在 AI 栏再点一次「{'下载实时模型' if live else '下载整片模型'}」接着下，"
                    f"或先换成已就绪的型号")
    except OSError:
        pass
    return (f"{label}模型 {name} 没下载：在 AI 栏点"
            f"「{'下载实时模型' if live else '下载整片模型'}」，或换成已就绪的型号")


def find_llama_binary(config: cfg.Config | None = None) -> str:
    """GUI 启动时 PATH 里没有 /opt/homebrew/bin，得自己补。"""
    if config is not None:
        wanted = str(config.get("translate.llama_binary", "") or "")
        if wanted and Path(wanted).is_file():
            return wanted
    found = shutil.which("llama-server")
    if found:
        return found
    for prefix in ("/opt/homebrew/bin", "/usr/local/bin"):
        path = f"{prefix}/llama-server"
        if Path(path).is_file():
            return path
    return ""


def download_llm_model(name: str, on_progress=None,
                       cancel: threading.Event | None = None,
                       on_note=None) -> Path:
    """和 whisper 模型一样：镜像优先、断点续传、短读超时自动重试。"""
    entry = LLM_MODELS.get(name)
    if not entry:
        raise TranslationError(f"不认识的语言模型：{name}")
    dest = llm_model_path(name)
    LLM_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    if llm_model_ready(name):
        return dest
    urls = [template.format(path=entry[2]) for template in _LLM_URLS]
    return download_from_mirrors(urls, dest, on_progress, cancel,
                                 error_cls=TranslationError, on_note=on_note)


class LlamaServer:
    """常驻一个 llama-server 进程。

    必须常驻：每批都重新加载 1~2GB 的模型，翻译几句就要等几十秒。它自带 OpenAI 兼容的
    /v1/chat/completions，所以请求格式和行号对齐协议可以整段复用在线那条路。
    """
    _lock = threading.Lock()
    _running: dict[str, tuple[subprocess.Popen, str]] = {}

    @classmethod
    def ensure(cls, binary: str, model: Path, timeout: float = 180.0) -> str:
        key = str(model)
        with cls._lock:
            slot = cls._running.get(key)
            if slot is not None and slot[0].poll() is None:
                return slot[1]
            port = _free_port()
            proc = subprocess.Popen(
                [binary, "-m", str(model), "--host", "127.0.0.1", "--port", str(port),
                 "-c", "4096", "--no-webui"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            base = f"http://127.0.0.1:{port}/v1"
            deadline = time.time() + timeout
            while time.time() < deadline:
                if proc.poll() is not None:
                    raise TranslationError(
                        f"llama-server 启动失败（退出码 {proc.returncode}）：模型文件可能损坏")
                if cls._healthy(port):
                    cls._running[key] = (proc, base)
                    atexit.register(cls.shutdown_all)
                    return base
                time.sleep(0.4)
            proc.terminate()
            raise TranslationError("llama-server 启动超时（第一次加载模型要几十秒，再试一次看看）")

    @staticmethod
    def _healthy(port: int) -> bool:
        try:
            with httpx.Client(timeout=2.0, trust_env=False) as client:
                return client.get(f"http://127.0.0.1:{port}/health").status_code == 200
        except Exception:
            return False

    @classmethod
    def shutdown_all(cls) -> None:
        with cls._lock:
            for proc, _ in cls._running.values():
                try:
                    proc.terminate()
                except Exception:
                    pass
            cls._running.clear()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class LlamaCpp(OpenAICompat):
    """本地离线翻译：llama.cpp + GGUF，走本机 llama-server。"""
    name = "llama"
    default_timeout = 300.0     # 本地推理一批 24 句可能要一两分钟

    def __init__(self, config: cfg.Config, context_lines: int = 0,
                 model: str | None = None):
        binary = find_llama_binary(config)
        if not binary:
            raise TranslationError("没有 llama.cpp：brew install llama.cpp")
        model = str(model or config.get("translate.llama_model", "") or "")
        if not llm_model_ready(model):
            raise TranslationError(f"本地模型没下载：{model or '（未选择）'}（AI 面板里点下载）")
        base = LlamaServer.ensure(binary, llm_model_path(model))
        super().__init__(base, "local-only", model, context_lines=context_lines)
        self.model_label = model
        if "qwen3.5" in model.lower():
            # Qwen3.5 默认带思考过程；翻译只要结果，关掉 thinking
            self.chat_template_kwargs = {"enable_thinking": False}


LIVE_MIN_BYTES = 700_000_000       # 0.5B 太小，实测会把日文原样吐回来


def _live_choice(installed: list[str]) -> str:
    """实时模型：在能正常翻的里面挑最小；只有 0.5B 时才退回 0.5B。"""
    if not installed:
        return ""
    usable = [name for name in installed
              if llm_model_path(name).stat().st_size >= LIVE_MIN_BYTES]
    return (usable or installed)[0]


def _local_model_for(config: cfg.Config, live: bool) -> str:
    """实时用已下载的、能正常翻的小模型；整片/一键用最大模型；缺了就兜底。"""
    key = "translate.live_llama_model" if live else "translate.llama_model"
    wanted = str(config.get(key, "") or "")
    if llm_model_ready(wanted):
        return wanted
    installed = [name for name in list_llm_models() if llm_model_ready(name)]
    installed.sort(key=lambda name: llm_model_path(name).stat().st_size)
    if installed:
        chosen = _live_choice(installed) if live else installed[-1]
        cfg.append_log(
            f"本地翻译模型分工：{'实时' if live else '整片'}指定的 {wanted or '空'} 不可用，"
            f"改用 {chosen}")
        return chosen
    return wanted


def build(config: cfg.Config, live: bool = False) -> Translator:
    section = config.data["translate"]
    engine = section.get("engine", "openai")
    context_lines = int(section.get("context_lines", 0) or 0)
    if engine == "deepl":
        return DeepL(cfg.secret_get("deepl"), free=bool(section.get("deepl_free", True)))
    if engine == "argos":
        return Argos()
    if engine == "llama":
        return LlamaCpp(config, context_lines=context_lines,
                        model=_local_model_for(config, live))
    return OpenAICompat(
        section.get("openai_base_url", "https://api.openai.com/v1"),
        cfg.secret_get("openai") or "",
        section.get("openai_model", "gpt-4o-mini"),
        context_lines=context_lines,
    )


def migrate_model_split(config: cfg.Config) -> bool:
    """一次性把模型分工落到配置：实时最小、整片最大（只按已下载的模型选）。"""
    if config.get("translate.models_split_v1", False):
        return False
    installed = [name for name in list_llm_models() if llm_model_ready(name)]
    installed.sort(key=lambda name: llm_model_path(name).stat().st_size)
    if installed:
        full, live = installed[-1], _live_choice(installed)
    else:
        full = "qwen2.5-1.5b-instruct-q4_k_m.gguf"
        live = "qwen2.5-0.5b-instruct-q4_k_m.gguf"
    config.set("translate.llama_model", full)
    config.set("translate.live_llama_model", live)
    config.set("translate.models_split_v1", True)
    cfg.append_log(f"翻译模型分工已设置：一键/整片={full} · 实时={live}")
    return True


def engine_labels() -> dict[str, str]:
    return {
        "openai": "在线 · OpenAI 兼容接口",
        "deepl": "在线 · DeepL",
        "llama": "本地 · llama.cpp + GGUF（离线）",
        "argos": "本地 · Argos（离线模型）",
    }
