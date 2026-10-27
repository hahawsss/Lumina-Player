"""界面语言：中文原文就是键，英文查表。

为什么中文当键：全项目 1100+ 条中文字面量散在调用点，改成语义 key 要动每一条，
收益只是「改中文不用改译文」；而中文当键漏翻时原样显示中文，不会炸，也不会出现空标签。

切语言是立即生效：每个界面对象把自己的「重刷文字」回调登记到 bind()，
set_language() 之后 retranslate() 挨个重刷。动态文案（进度、状态）在调用点用 t()
取词，下一次刷新自然会换语言。
"""
from __future__ import annotations

import weakref
from typing import Callable

from .i18n_en import EN

LANGUAGES = (("auto", "跟随系统 / System"), ("zh", "中文"), ("en", "English"))

_lang = "zh"
_registry: list[tuple[weakref.ref, Callable[[], None]]] = []
_missing: set[str] = set()


def resolve(pref: str) -> str:
    """auto = 跟随系统；判断不出来时按英文（海外用户是默认受众）。"""
    if pref in ("zh", "en"):
        return pref
    try:
        from PySide6.QtCore import QLocale
        name = QLocale.system().name()
    except Exception:
        name = ""
    if name.lower().startswith("zh"):
        return "zh"
    return "en" if name else "zh"


def current() -> str:
    return _lang


def set_language(lang: str) -> None:
    global _lang
    _lang = lang if lang in ("zh", "en") else "zh"


def t(key: str, *args, **kw) -> str:
    text = key if _lang == "zh" else EN.get(key, key)
    if _lang != "zh" and key not in EN and any("\u4e00" <= ch <= "\u9fff" for ch in key):
        _missing.add(key)
    return text.format(*args, **kw) if (args or kw) else text


def missing() -> set[str]:
    return set(_missing)


def bind(owner, refresh: Callable[[], None]) -> None:
    """登记「这个对象怎么重刷文字」，并立刻刷一次。"""
    _registry.append((weakref.ref(owner), refresh))
    refresh()


def retranslate() -> None:
    global _registry
    alive = []
    for ref, refresh in _registry:
        if ref() is None:
            continue
        alive.append((ref, refresh))
        try:
            refresh()
        except RuntimeError:      # 控件已销毁（Qt 会抛 RuntimeError）
            continue
    _registry = alive
