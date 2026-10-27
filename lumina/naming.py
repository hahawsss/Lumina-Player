"""字幕产物的文件名规则：新产物跟界面语言走，但两种后缀都认。

中文界面仍写 `<片名>.原文.srt` / `<片名>.<语言>译文.srt` / `<片名>.双语.ass`；
英文界面写 `<name>.original.srt` / `<name>.<Lang>.translated.srt` / `<name>.bilingual.ass`。
挂回字幕时两种都找：旧产物不重命名、不迁移 —— 改名会让播放列表与缓存里的旧引用全部失配。
"""
from __future__ import annotations

from pathlib import Path

from . import i18n
from .i18n_en import EN as _EN

ORIGINAL = ("原文", "original")
BILINGUAL = ("双语", "bilingual")


def lang_name(display_zh: str) -> str:
    """按当前界面语言给语言名：中文界面「中文」，英文界面 Chinese。"""
    if i18n.current() == "en":
        return _EN.get(display_zh, display_zh)
    return display_zh


def lang_names_both(display_zh: str) -> list[str]:
    return list(dict.fromkeys([display_zh, _EN.get(display_zh, display_zh)]))


def _pick(pair: tuple[str, str]) -> str:
    return pair[1] if i18n.current() == "en" else pair[0]


def original_name(stem: str) -> str:
    return f"{stem}.{_pick(ORIGINAL)}.srt"


def bilingual_name(stem: str) -> str:
    return f"{stem}.{_pick(BILINGUAL)}.ass"


def translated_name(stem: str, display_zh: str, arrow: str = "") -> str:
    if i18n.current() == "en":
        return f"{stem}.{arrow}{lang_name(display_zh)}.translated.srt"
    return f"{stem}.{arrow}{display_zh}译文.srt"


def original_candidates(stem: str) -> list[Path]:
    return [Path(f"{stem}.{suffix}.srt") for suffix in dict.fromkeys(ORIGINAL)]


def bilingual_candidates(stem: str) -> list[Path]:
    return [Path(f"{stem}.{suffix}.ass") for suffix in dict.fromkeys(BILINGUAL)]


def translated_globs(stem: str, display_zh: str) -> list[str]:
    """译文 .srt 的查找模式：两种语言名 × 两种后缀写法，允许中间夹「源→」前缀。"""
    globs: list[str] = []
    for name in lang_names_both(display_zh) if display_zh else []:
        globs += [f"{stem}.{name}译文.srt", f"{stem}.*{name}译文.srt",
                  f"{stem}.{name}.translated.srt", f"{stem}.*{name}.translated.srt"]
    globs += [f"{stem}.*译文.srt", f"{stem}.*.translated.srt"]
    return list(dict.fromkeys(globs))


def first_existing(folder: Path, names: list[Path]) -> Path | None:
    """当前语言的那份优先；另一语言的同名旧产物也认。"""
    for name in names:
        candidate = folder / name
        if candidate.exists():
            return candidate
    return None


def naming_example(stem: str) -> str:
    """设置页里展示的「这一部会落成哪三份」，跟着当前语言走。"""
    if i18n.current() == "en":
        return (f"{stem}.original.srt / {stem}.<Lang>.translated.srt / "
                f"{stem}.bilingual.ass")
    return f"{stem}.原文.srt / {stem}.<语言>译文.srt / {stem}.双语.ass"
