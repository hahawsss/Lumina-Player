"""媒体文件里的语言标签非常野：中文可能是 zh / zho / chi / cmn / chs / cht。

这里做归一化，并把用户偏好展开成 mpv 能匹配的全套变体（mpv 自己不做三字母→两字母转换）。
"""
from __future__ import annotations

# 三字母(含 bibliographic) / 别称 -> ISO 639-1 基础子标签
_TO_BASE = {
    "zho": "zh", "chi": "zh", "cmn": "zh", "chs": "zh", "cht": "zh", "yue": "zh",
    "eng": "en", "jpn": "ja", "kor": "ko", "fra": "fr", "fre": "fr", "deu": "de",
    "ger": "de", "spa": "es", "rus": "ru", "ita": "it", "por": "pt", "tha": "th",
    "vie": "vi", "ind": "id", "ara": "ar", "pol": "pl", "nld": "nl", "dut": "nl",
    "tur": "tr", "ukr": "uk", "swe": "sv", "nob": "no", "fin": "fi", "dan": "da",
    "ces": "cs", "cze": "cs", "ell": "el", "gre": "el", "heb": "he", "hun": "hu",
    "ron": "ro", "rum": "ro", "bul": "bg", "ben": "bn", "tam": "ta", "tel": "te",
    "hin": "hi", "may": "ms", "msa": "ms", "per": "fa", "fas": "fa", "id": "id",
}

# 基础子标签 -> 媒体里出现过的所有写法（顺序即 mpv 匹配优先级）
_VARIANTS = {
    "zh": ["zh", "zho", "chi", "cmn", "chs", "cht", "yue"],
    "en": ["en", "eng"],
    "ja": ["ja", "jpn"],
    "ko": ["ko", "kor"],
    "fr": ["fr", "fra", "fre"],
    "de": ["de", "deu", "ger"],
    "es": ["es", "spa"],
    "ru": ["ru", "rus"],
    "it": ["it", "ita"],
    "pt": ["pt", "por"],
    "th": ["th", "tha"],
    "vi": ["vi", "vie"],
    "id": ["id", "ind"],
    "ar": ["ar", "ara"],
    "pl": ["pl", "pol"],
    "nl": ["nl", "nld", "dut"],
    "tr": ["tr", "tur"],
    "uk": ["uk", "ukr"],
    "sv": ["sv", "swe"],
    "no": ["no", "nob", "nno"],
    "fi": ["fi", "fin"],
    "da": ["da", "dan"],
    "cs": ["cs", "ces", "cze"],
    "el": ["el", "ell", "gre"],
    "he": ["he", "heb"],
    "hu": ["hu", "hun"],
    "ro": ["ro", "ron", "rum"],
    "bg": ["bg", "bul"],
    "hi": ["hi", "hin"],
    "ms": ["ms", "msa", "may"],
    "fa": ["fa", "per", "fas"],
}

# 变体 -> 基础（由上表反推，外加两字母自身）
_TO_BASE.update({v: k for k, values in _VARIANTS.items() for v in values})


def base_of(tag: str | None) -> str:
    """把任意写法压成 ISO 639-1 两字母；不认识的原样返回小写。"""
    if not tag:
        return ""
    cleaned = str(tag).strip().lower().replace("_", "-")
    if not cleaned or cleaned in ("und", "---", "0", "no", "n/a"):
        return ""
    head = cleaned.split("-")[0]
    return _TO_BASE.get(head, head)


def same_lang(a: str | None, b: str | None) -> bool:
    ba, bb = base_of(a), base_of(b)
    return bool(ba) and ba == bb


def rank_of(tag: str | None, prefs: list[str]) -> int:
    """tag 在偏好列表里的名次，越小越优先；不匹配返回 -1。"""
    base = base_of(tag)
    if not base:
        return -1
    for index, pref in enumerate(prefs):
        if base == base_of(pref):
            return index
    return -1


def best_index(tags: list[str | None], prefs: list[str]) -> int:
    """在 tags 里挑排名最好的一条，返回其下标；全不匹配返回 -1。"""
    winner, best = -1, 1 << 30
    for index, tag in enumerate(tags):
        rank = rank_of(tag, prefs)
        if rank >= 0 and rank < best:
            winner, best = index, rank
    return winner


def expand_for_mpv(prefs: list[str]) -> str:
    """展开成逗号分隔串给 mpv 的 alang/slang，保留偏好顺序、去掉重复变体。"""
    seen: set[str] = set()
    out: list[str] = []
    for pref in prefs:
        base = base_of(pref)
        if not base:
            continue
        for variant in _VARIANTS.get(base, [base]):
            if variant not in seen:
                seen.add(variant)
                out.append(variant)
    return ",".join(out)


def to_translate_code(tag: str | None) -> str:
    """翻译成翻译服务认得的 code；中文按简体处理。"""
    base = base_of(tag)
    if not base:
        return "en"
    if base == "zh":
        lowered = (tag or "").lower()
        return "zh-TW" if ("tw" in lowered or "hk" in lowered or "cht" in lowered) else "zh-CN"
    return base


def language_name(tag: str | None) -> str:
    from .config import lang_display
    return lang_display(tag)
