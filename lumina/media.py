"""播放素材来源：本地文件或云端 WebDAV，统一给播放器/转写/字幕使用。

本地路径的 key 仍是路径本身，保证旧的字幕缓存/完成标记继续命中；
云端 key 用 `115:<网盘路径>`，端口变化或直链刷新都不会影响自动恢复。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import unquote

from . import config as cfg

_SCHEME_ONE_SLASH = re.compile(r"^(https?):/(?!/)")


def normalize_uri(uri: str) -> str:
    """mpv 回报的 path 会把 `http://` 收成 `http:/`；比较素材时先补回来。"""
    fixed = _SCHEME_ONE_SLASH.sub(r"\1://", uri or "")
    return unquote(fixed)


@dataclass
class MediaSource:
    uri: str
    display_name: str
    key: str
    origin: str = "local"                 # local | 115
    headers: dict[str, str] = field(default_factory=dict)
    user_agent: str = ""
    # 云端 token 可能失效；ASR/重新开流前用它换一份新的 header，不落盘
    refresh: Callable[[], dict[str, str]] | None = field(
        default=None, repr=False, compare=False)
    # 单流代理优先用 OpenList 的 /p/ 下载地址（不受 115 单链 100MB 限制）
    upstream: Callable[[], str] | None = field(
        default=None, repr=False, compare=False)

    def matches(self, path: str) -> bool:
        if self.is_remote:
            return normalize_uri(self.uri) == normalize_uri(path)
        try:
            # mpv 回报的是绝对路径；从「最近打开」进来的相对路径也要对上
            return Path(self.uri).expanduser().resolve() == Path(path).expanduser().resolve()
        except (OSError, RuntimeError):
            return self.uri == path

    def via_proxy(self, uri: str) -> "MediaSource":
        """同一条素材的本机代理地址：key/字幕身份不变，只换读取入口。"""
        return MediaSource(uri=uri, display_name=self.display_name, key=self.key,
                           origin=self.origin, headers={}, user_agent="")

    @classmethod
    def local(cls, path: str | Path) -> "MediaSource":
        raw = str(Path(path).expanduser())
        return cls(uri=raw, display_name=Path(raw).name, key=raw, origin="local")

    @property
    def is_remote(self) -> bool:
        return self.origin != "local"

    @property
    def stem(self) -> str:
        return cfg.safe_name(Path(self.display_name).stem)

    @property
    def folder_name(self) -> str:
        if not self.is_remote:
            return self.stem
        tag = hashlib.sha1(self.key.encode("utf-8")).hexdigest()[:8]
        return f"{self.stem}-{tag}"

    def ffmpeg_header_text(self) -> str:
        if not self.headers:
            return ""
        return "".join(f"{name}: {value}\r\n" for name, value in self.headers.items())

    def mpv_header_fields(self) -> str:
        return ", ".join(f"{name}: {value}" for name, value in self.headers.items())
