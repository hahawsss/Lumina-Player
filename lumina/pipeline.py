"""字幕管线：原字幕/AI 转写 → 翻译 → 生成 ASS → 挂给 mpv，全程后台可取消。

规则表（和 UI 上的选择一一对应）：
  原文     用 mpv 原生轨道，样式完全交给原字幕文件
  双语     我们自己生成一条 ASS：原文一行 + 译文一行（各自样式，位置可上下换）
  译文     同一条 ASS 只留译文行
生成轨和原生轨互斥（sid 只能选一条），所以切模式 = 切轨道，不会叠两层。
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from difflib import SequenceMatcher
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from . import asr, config as cfg, i18n, langtags, naming, subtitles, translate
from .media import MediaSource
from .subtitles import AssStyle, Cue

BITMAP_CODECS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "dvb_teletext",
                 "pgssub", "vobsub"}

_AUTH_ERROR_MARKERS = ("401", "403", "Unauthorized", "Forbidden", "authorization failed")


def _is_auth_error(exc: BaseException) -> bool:
    text = str(exc)
    return any(marker in text for marker in _AUTH_ERROR_MARKERS)


class PipelineError(RuntimeError):
    pass


@dataclass
class SourceInfo:
    cues: list[Cue]
    lang: str
    origin: str          # external | embedded | ai
    path: Path | None = None


def ffprobe_bin() -> str:
    return asr.find_binary("ffprobe")


def subtitle_streams(media: str,
                     headers: dict[str, str] | None = None,
                     user_agent: str = "") -> list[dict]:
    try:
        args = [ffprobe_bin(), "-v", "error"]
        if headers:
            args += ["-headers", asr._header_text(headers)]
        if user_agent:
            args += ["-user_agent", user_agent]
        args += ["-print_format", "json", "-show_streams",
                 "-select_streams", "s", str(media)]
        proc = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PipelineError(f"ffprobe 失败: {exc}") from exc
    if proc.returncode != 0:
        raise PipelineError(f"ffprobe 失败: {proc.stderr.strip()[:200]}")
    data = json.loads(proc.stdout or "{}")
    rows = []
    for stream in data.get("streams", []):
        tags = stream.get("tags") or {}
        rows.append({
            "index": int(stream.get("index", -1)),
            "codec": (stream.get("codec_name") or "").lower(),
            "lang": (tags.get("language") or stream.get("language") or "").lower(),
            "title": tags.get("title") or "",
        })
    return rows


class SubtitlePipeline(QObject):
    progress = Signal(str, int, int)      # stage, done, total
    notice = Signal(str)
    error = Signal(str)
    busy = Signal(bool)
    asr_finished = Signal()               # 转写完成（翻译可能还在后台跑）
    languages_found = Signal(str, str)    # 音轨语言, 字幕语言

    def __init__(self, engine, config: cfg.Config, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.config = config
        self.cache = translate.Cache(enabled=bool(config.get("translate.cache", True)))
        self.whisper = asr.Whisper(config)
        self.mode = config.get("languages.subtitle_display", "bilingual")
        self.target = config.get("languages.translation_target", "zh-CN")
        self.source: SourceInfo | None = None
        self.cues: list[Cue] = []
        self.translated: list[str] = []
        self.output_path: Path | None = None
        self.original_path: Path | None = None
        self.sub_folder: Path = cfg.SUBTITLE_DIR
        self.media_stem = "lumina"
        self.ai_pid = None
        self.current_media: str | None = None
        self.media_source: MediaSource | None = None
        self.media_key: str = ""
        self._cancel: threading.Event | None = None
        self._threads: list[threading.Thread] = []
        self._cond = threading.Condition()
        self._write_lock = threading.Lock()
        self._last_render = 0.0
        self._pending_indices: list[int] = []
        self._live_session = False
        self.live_output_path: Path = cfg.LIVE_DIR / "lumina.live.ass"
        self._active_translator = None
        self._transcript_verified = False
        self._duration_cache: tuple[str, float] = ("", 0.0)
        self._asr_running = threading.Event()
        self._asr_lang_hint = ""
        self._lang_notice_key: tuple | None = None
        self._lang_log_key: tuple | None = None
        self.player_released = False
        self.audio_index_override: int | None = None
        self.audio_lang_override = ""
        self.cloud_live = False
        # 主窗口按「缓存装得下多少秒」算出来的预读上限；0 = 不加这层限制。
        # 秒数不看码率：高码率片子里 60 秒可能比整个缓存还大，会把单流代理拖进重置风暴。
        self.cloud_lead_cap = 0.0
        self._live_lang_checked = False

    # ------------------------------------------------------------ 生命周期

    def begin_media(self, media: str | MediaSource,
                    source: MediaSource | None = None) -> None:
        self.stop_all()
        if isinstance(media, MediaSource):
            source = media
            media = source.uri
        source = source or MediaSource.local(media)
        self.current_media = media
        self.media_source = source
        self.media_key = source.key
        self.source = None
        self.cues = []
        self.translated = []
        self.ai_pid = None
        self.media_stem = source.stem
        self._live_session = False
        self._transcript_verified = False
        self._duration_cache = ("", 0.0)
        self._asr_running.clear()
        self._lang_notice_key = None
        self._lang_log_key = None
        self.player_released = False
        self.audio_index_override = None
        self.audio_lang_override = ""
        self.live_output_path = cfg.LIVE_DIR / f"{self.media_stem}.live.ass"
        wanted = str(self.config.get("subtitle.output_dir", "") or "").strip()
        if source.is_remote:
            root = cfg.subtitle_root(wanted) / (
                "115" if source.origin == "115" else "remote")
            self.sub_folder = root / source.folder_name
        else:
            next_to_video = bool(self.config.get("subtitle.next_to_video", False))
            root = cfg.subtitle_root_for(media, wanted, next_to_video)
            if next_to_video:
                try:
                    followed = root == Path(media).expanduser().parent.resolve()
                except OSError:
                    followed = False
                if not followed:
                    self.notice.emit(f"视频目录写不进去，这一部的字幕落到 {root}")
            elif wanted:
                try:
                    usable = root == Path(wanted).expanduser().resolve()
                except OSError:
                    usable = False
                if not usable:
                    self.notice.emit(f"输出目录「{wanted}」用不了，这一部落到 {root}")
            self.sub_folder = root / self.media_stem
        try:
            self.sub_folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.sub_folder = cfg.SUBTITLE_DIR / source.folder_name
            self.sub_folder.mkdir(parents=True, exist_ok=True)
        self._set_normal_output()

    def _set_normal_output(self) -> None:
        # 新产物按当前界面语言命名；已经存在另一种语言命名的旧产物就继续用它 ——
        # 不重命名、不迁移，重开片子也还能挂回来
        self.output_path = (naming.first_existing(
            self.sub_folder, naming.bilingual_candidates(self.media_stem))
            or self.sub_folder / naming.bilingual_name(self.media_stem))
        self.original_path = (naming.first_existing(
            self.sub_folder, naming.original_candidates(self.media_stem))
            or self.sub_folder / naming.original_name(self.media_stem))

    # ------------------------------------------------------------ 成片完整性

    def _media_duration(self) -> float:
        """片长：优先问正在播放的引擎，拿不到再让 ffprobe 单独问一次。"""
        media = self.current_media or ""
        cached_media, cached = self._duration_cache
        if media and cached_media == media and cached > 0:
            return cached
        duration = 0.0
        try:
            value = self.engine.duration
            if value:
                duration = float(value)
        except Exception:
            duration = 0.0
        if duration <= 0 and media:
            duration = asr.media_duration(
                media, self.media_source.headers if self.media_source else None,
                self.media_source.user_agent if self.media_source else "")
        duration = max(0.0, duration)
        if duration > 0:
            self._duration_cache = (media, duration)
        return duration

    def _refresh_media_headers(self) -> bool:
        """ASR/重新开流前换一份云端 header；OpenList token 失效时这是唯一自愈点。"""
        source = self.media_source
        if source is None or not source.is_remote or not callable(source.refresh):
            return False
        try:
            headers = source.refresh()
        except Exception as exc:
            cfg.append_log(f"云端 token 刷新失败：{type(exc).__name__}: {exc}")
            return False
        if not headers:
            return False
        source.headers.clear()
        source.headers.update(headers)
        return True

    def _marker_path(self) -> Path | None:
        """完成标记按稳定媒体 key 区分：本地路径或 115 网盘路径，不用临时 URL。"""
        key = self.media_key or (
            str(Path(self.current_media).expanduser()) if self.current_media else "")
        if not key:
            return None
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
        return cfg.CACHE_DIR / "transcripts" / f"{digest}.json"

    def _read_marker(self) -> dict:
        path = self._marker_path()
        if path is None or not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_marker(self, complete: bool, origin: str | None = None) -> None:
        path = self._marker_path()
        if path is None:
            return
        cues = self.cues if complete else []
        payload = {
            "version": 1,
            "asr_version": asr.TRANSCRIPT_VERSION,
            "complete": bool(complete),
            "origin": origin or (self.source.origin if self.source else "ai"),
            "lang": (self.source.lang if self.source else "") or "",
            "duration": round(self._media_duration(), 3),
            "cues": len(cues),
            "last_end": round(max((cue.end for cue in cues), default=0.0), 3),
            "audio_index": self._audio_ordinal(),
            "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            temp.replace(path)
        except OSError as exc:
            cfg.append_log(f"写转写完成标记失败：{exc}")

    def transcript_complete(self) -> bool:
        """只有本版本完整跑完并写过完成标记的转写才算数。

        旧版留下的半截字幕/被停掉的任务没有标记，一键翻译会重新转写，
        避免把 16 句残片当成整片继续翻译。
        """
        if not self.cues:
            return False
        if self._transcript_verified:
            return True
        marker = self._read_marker()
        if not marker or not marker.get("complete"):
            return False
        try:
            origin = str(marker.get("origin") or "ai")
            if origin == "ai" and \
                    int(marker.get("asr_version", 0) or 0) != asr.TRANSCRIPT_VERSION:
                return False       # 旧转写缺片头/带旧幻觉，必须按新算法重跑
            if int(marker.get("cues", -1)) != len(self.cues):
                return False
            if origin == "ai" and \
                    int(marker.get("audio_index", 0)) != self._audio_ordinal():
                return False
            duration = self._media_duration()
            if duration > 0:
                saved = float(marker.get("duration", 0.0))
                if abs(saved - duration) > max(3.0, duration * 0.02):
                    return False
                last = max(cue.end for cue in self.cues)
                if abs(float(marker.get("last_end", -1.0)) - last) > 2.0:
                    return False
        except (TypeError, ValueError):
            return False
        self._transcript_verified = True
        return True

    def transcript_language(self) -> str:
        if self.source is not None and self.source.lang:
            return langtags.base_of(self.source.lang)
        marker = self._read_marker()
        return langtags.base_of(str(marker.get("lang") or ""))

    def transcript_matches_language(self, hint: str) -> bool:
        """现成字幕是不是用户现在指定的语言；不一致就不能拿旧转写直接翻译。"""
        wanted = langtags.base_of(hint)
        if not wanted or wanted == "auto":
            return True
        current = self.transcript_language()
        return not current or current == wanted

    def untranslated_count(self) -> int:
        """统计没有真正翻成目标语言的句子：空译文，或模型把原文原样返回。"""
        source = (self.source.lang if self.source is not None and self.source.lang
                  else self._asr_lang_hint)
        left = 0
        for index, cue in enumerate(self.cues):
            text = self.translated[index] if index < len(self.translated) else ""
            if not text or translate.looks_untranslated(cue.plain(), text, source, self.target):
                left += 1
        return left

    def restore_existing(self) -> bool:
        """打开片子时自动挂回上次生成的字幕；只读文件，不重跑 ASR/翻译。"""
        if self.is_busy() or not self.output_path or not self.output_path.exists():
            return False
        if self.ai_pid is not None:
            return True
        cues: list[Cue] = []
        if self.original_path and self.original_path.exists():
            try:
                cues = subtitles.parse_srt(self.original_path.read_text(encoding="utf-8"))
            except OSError:
                cues = []
        if not cues:
            # 没有原文 sidecar 也要能看：直接把上次的 ASS 挂上
            self.ai_pid = self.engine.add_subtitle(str(self.output_path), select=True)
            return self.ai_pid is not None
        self.cues = cues
        self.translated = [""] * len(cues)
        self.target = self.config.get("languages.translation_target", self.target)
        target_name = cfg.lang_display(self.target) if self.target else i18n.t("译文")
        marker = self._read_marker()
        source_lang = str(marker.get("lang") or "") or langtags.base_of(self._audio_lang())
        source_name = (cfg.safe_name(naming.lang_name(cfg.lang_display(source_lang)), 12)
                       if source_lang else "")
        patterns: list[str] = []
        if source_name and not (self.target and langtags.same_lang(source_lang, self.target)):
            # 只约束「源→」前缀：目标语言后缀是中/英两种写法都认
            patterns.append(f"{self.media_stem}.{source_name}→*")
        patterns += naming.translated_globs(self.media_stem, target_name)
        candidates: list[Path] = []
        for pattern in patterns:
            candidates = sorted(self.sub_folder.glob(pattern),
                                key=lambda p: p.stat().st_mtime, reverse=True)
            if candidates:
                break
        for path in candidates:
            if source_name and "→" in path.name:
                # 日文字幕不能捡上次「英文→中文」的译文，哪怕句数碰巧一样
                prefix = path.name.split(".", 1)[1].split("→", 1)[0]
                if prefix != source_name:
                    continue
            try:
                lines = subtitles.parse_srt(path.read_text(encoding="utf-8"))
            except OSError:
                continue
            if len(lines) == len(cues):
                self.translated = [line.plain() for line in lines]
                break
        self.source = SourceInfo(list(self.cues), source_lang, "ai", self.output_path)
        self.mode = self.config.get("languages.subtitle_display", self.mode)
        if not any(self.translated):
            self.mode = "original"
        # 用当前样式/折行规则重写一遍：上次生成的长行也会按新规则折成两行/缩字号
        self._render(force=True)
        if self.ai_pid is None:
            return False
        legacy = "" if self.transcript_complete() else \
            "（旧版，未确认整片完整；点「一键翻译」会重新转写）"
        self.notice.emit(f"已自动载入上次生成的字幕：{len(self.cues)} 句{legacy}")
        return True

    def stop_all(self) -> None:
        if self._cancel:
            self._cancel.set()
        for thread in self._threads:
            thread.join(timeout=1.2)
        self._threads = []
        if self.ai_pid is not None and self.engine.is_alive():
            try:
                self.engine.remove_subtitle(self.ai_pid)
            except Exception:
                pass
        self.ai_pid = None
        self._active_translator = None
        self._asr_running.clear()
        self.player_released = False
        self.audio_index_override = None
        self.audio_lang_override = ""
        self.cloud_live = False
        if self._live_session:
            try:
                self.live_output_path.unlink(missing_ok=True)
            except OSError:
                pass
            self._live_session = False
            self._set_normal_output()
        with self._cond:
            self._pending_indices = []
        self.busy.emit(False)

    def is_busy(self) -> bool:
        return any(t.is_alive() for t in self._threads)

    # ------------------------------------------------------------ 样式/输出

    def _style(self) -> AssStyle:
        section = self.config.data["subtitle"]
        size = int(max(20, min(120, round(float(section["size"])))))
        return AssStyle(
            font=section["font"],
            size=size,
            translated_size=int(size * (0.86 if self.mode == "bilingual" else 1.0)),
            outline=int(max(0, min(8, round(float(section["outline"]))))),
            shadow=int(max(0, min(8, round(float(section["shadow"]))))),
            margin_v=int(max(8, min(200, round(float(section["margin"]))))),
            bold=bool(section["bold"]),
        )

    def translated_path(self) -> Path:
        """译文 .srt 的文件名带上目标语言；从别种字幕翻过来的，再带上源语言。"""
        target = cfg.lang_display(self.target) if self.target else i18n.t("译文")
        src = (self.source.lang if self.source else "") or ""
        arrow = ""
        if src and not (self.target and langtags.same_lang(src, self.target)):
            arrow = f"{cfg.safe_name(naming.lang_name(cfg.lang_display(src)), 12)}→"
        return (self.sub_folder / naming.translated_name(self.media_stem, target, arrow))

    def _write_sidecars(self) -> None:
        """双语 .ass 之外再落两份纯文本：原文 .srt 和译文 .srt，方便拷去别处用。

        时间轴和 .ass 完全同源（同一个 cues），只有文本不同，不会错位。
        """
        if not self.cues or not self.original_path:
            return
        subtitles.write_srt(self.original_path, self.cues)
        pairs = [(cue, text) for cue, text in zip(self.cues, self.translated) if text]
        if pairs:
            subtitles.write_srt(self.translated_path(),
                                [Cue(cue.start, cue.end, [text]) for cue, text in pairs])

    def _render(self, force: bool = False) -> None:
        if not self.output_path or not self.cues:
            return
        now = time.time()
        if not force and now - self._last_render < 2.0:
            return
        self._last_render = now
        with self._write_lock:
            translated = list(self.translated)
            subtitles.write_ass(
                self.output_path, self.cues, self._style(),
                translated=translated if self.mode in ("translated", "bilingual") else None,
                keep_original=(self.mode == "bilingual"),
                position=self.config.get("subtitle.translated_position", "below"),
            )
            if not self._live_session:
                self._write_sidecars()
        if self.player_released:
            return
        if self.ai_pid is None:
            self.ai_pid = self.engine.add_subtitle(str(self.output_path), select=True)
            if self.ai_pid is None:
                self.error.emit("字幕轨挂载失败：mpv 没有接受生成的字幕文件")
                return
        else:
            try:
                # 只在生成轨确实被选中时刷新：mpv 的 sub-reload 在当前轨是内嵌轨/关闭时
                # 会返回 LOADING_FAILED(-12)，每 2 秒刷一次就成了底部不断的红字
                if self.engine.get("sid") == self.ai_pid:
                    self.engine.reload_subtitle()
            except Exception as exc:
                self.error.emit(f"刷新字幕轨失败：{exc}")

    # ------------------------------------------------------------ 模式

    def set_display(self, mode: str) -> None:
        self.mode = mode
        self.config.set("languages.subtitle_display", mode)
        if mode == "original":
            if self.ai_pid is not None and self.engine.is_alive():
                try:
                    self.engine.remove_subtitle(self.ai_pid)
                except Exception:
                    pass
                self.ai_pid = None
            native = getattr(self, "native_sid", None)
            self.engine.set("sid", native if native is not None else "no")
            return
        if self.source is None:
            # 用户的心智模型是「选了轨 → 点双语就该出双语」，所以这里自己找当前轨，
            # 不要求他先点一次「翻译这条字幕」。
            current = next((t for t in self.engine.tracks_of("sub") if t.get("selected")), None)
            if current is not None and current.get("id") != self.ai_pid:
                self.notice.emit(f"正在取回 {langtags.language_name(current.get('lang'))}字幕原文…")
                self.translate_track(current)
                return
            self.notice.emit("没有可用的字幕来源：先选一条字幕，或在 AI 面板生成字幕")
            return
        self._spawn(self._translate_existing, "翻译字幕")

    def cycle_display(self) -> str:
        order = ["original", "bilingual", "translated"]
        self.set_display(order[(order.index(self.mode) + 1) % len(order)] if self.mode in order else "bilingual")
        return self.mode

    # ------------------------------------------------------------ 翻译已有字幕

    def resolve_source(self, track: dict) -> SourceInfo:
        media = self.current_media
        if not media:
            raise PipelineError("没有正在播放的文件")
        subs = [t for t in self.engine.tracks_of("sub") if not t.get("external")]
        if track.get("external"):
            path = self._find_external_file(track)
            if path is None:
                raise PipelineError("这条外挂字幕我定位不到文件路径，请用面板里的「添加字幕文件」重新加载")
            cues = subtitles.load_subtitle(path)
            if not cues:
                raise PipelineError(f"{path.name} 解析出来是空的")
            return SourceInfo(cues, track.get("lang") or "", "external", path)

        codec = (track.get("codec") or "").lower()
        if codec in BITMAP_CODECS:
            raise PipelineError("这是图形字幕（PGS/VobSub 位图），只能 OCR 才能转文字，本播放器暂未内置 OCR")
        nth = next((i for i, t in enumerate(subs) if t.get("id") == track.get("id")), -1)
        if nth < 0:
            raise PipelineError("这条字幕已经不是内嵌轨了")
        headers = self.media_source.headers if self.media_source else None
        user_agent = self.media_source.user_agent if self.media_source else ""
        streams = subtitle_streams(media, headers=headers, user_agent=user_agent)
        known = {row["index"] for row in streams}
        ff_index = track.get("ff-index")     # mpv 直接给了 ffmpeg 流号，比按位置猜准
        if isinstance(ff_index, int) and ff_index in known:
            target_index = ff_index
        else:
            target_index = None
            if nth < len(streams):
                target_index = streams[nth]["index"]
                if track.get("lang") and streams[nth]["lang"] and \
                        not langtags.same_lang(track["lang"], streams[nth]["lang"]):
                    alt = next((s for s in streams if langtags.same_lang(s["lang"], track["lang"])), None)
                    if alt:
                        target_index = alt["index"]
        if target_index is None:
            raise PipelineError("用 ffprobe 没找到对应的字幕流")
        out = (Path(cfg.WORK_DIR) / self.media_stem
               / f"source-{track.get('id')}.srt")
        out.parent.mkdir(parents=True, exist_ok=True)
        for codec_out in ("srt", "ass"):
            args = [asr.find_binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y"]
            if headers:
                args += ["-headers", asr._header_text(headers)]
            if user_agent:
                args += ["-user_agent", user_agent]
            args += ["-i", str(media), "-map", f"0:{target_index}",
                     "-c:s", codec_out, str(out)]
            proc = subprocess.run(args, capture_output=True, text=True, timeout=180)
            if proc.returncode == 0 and out.exists() and out.stat().st_size > 8:
                break
        else:
            raise PipelineError(f"抽取内嵌字幕失败：{proc.stderr.strip()[:200]}")
        if codec_out == "srt":
            cues = subtitles.load_subtitle(out)
        else:
            cues = subtitles.parse_ass(out.read_text(encoding="utf-8", errors="replace"))
        if not cues:
            raise PipelineError("抽出来的字幕是空的（可能是图形字幕）")
        return SourceInfo(cues, track.get("lang") or "", "embedded", out)

    def _find_external_file(self, track: dict) -> Path | None:
        if not self.current_media or (self.media_source and self.media_source.is_remote):
            return None
        stem = Path(self.current_media)
        candidates = [p for p in stem.parent.iterdir() if p.suffix.lower() in cfg.SUB_EXTS]
        title = (track.get("title") or "").lower()
        lang = langtags.base_of(track.get("lang") or "")
        scored: list[tuple[int, Path]] = []
        for path in candidates:
            tag = path.stem.lower()
            score = 0
            if lang and lang in tag:
                score += 2
            if title and title in tag:
                score += 1
            scored.append((score, path))
        if not scored:
            return None
        scored.sort(key=lambda item: (-item[0], item[1].name))
        return scored[0][1] if scored[0][0] > 0 else None

    def translate_track(self, track: dict) -> None:
        self._spawn(self._translate_track_worker, "翻译字幕", track)

    def _translate_track_worker(self, cancel: threading.Event, track: dict) -> None:
        self.source = self.resolve_source(track)
        self.native_sid = track.get("id")
        self._adopt_cues(self.source.cues)
        self.languages_found.emit(self._audio_lang(), self.source.lang or self.source.origin)
        self._translate_all(cancel)

    def _adopt_cues(self, cues: list[Cue]) -> None:
        self.cues = list(cues)
        self.translated = [""] * len(self.cues)
        with self._cond:
            self._pending_indices = list(range(len(self.cues)))

    @staticmethod
    def _norm_text(text: str) -> str:
        return re.sub(r"[\s\W_]+", "", text).lower()

    @classmethod
    def _texts_similar(cls, a: str, b: str) -> bool:
        """重叠窗口里同一句常被 whisper 吐成轻微不同的文本；不能只比完全相等。"""
        if a == b:
            return True
        if not a or not b:
            return False
        if len(a) >= 4 and (a in b or b in a):
            return True
        if min(len(a), len(b)) < 4:
            return False
        return SequenceMatcher(None, a, b).ratio() >= 0.82

    def _append_cue(self, cue: Cue) -> bool:
        """重叠窗口/回跳重转会把同一句再吐一次；相似句只延长/替换，不新增。"""
        norm = self._norm_text(cue.plain())
        if norm:
            # 不只看最后一条：回跳后会重新转写较早的时间段，要能在整个附近时间段里找到旧句
            for index in range(len(self.cues) - 1, -1, -1):
                existing = self.cues[index]
                if existing.end < cue.start - 2.5:
                    break
                if existing.start > cue.end + 2.5:
                    continue
                if not self._texts_similar(norm, self._norm_text(existing.plain())):
                    continue
                if existing.start < cue.end + 1.5 and cue.start < existing.end + 1.5:
                    existing.start = min(existing.start, cue.start)
                    existing.end = max(existing.end, cue.end)
                    if len(cue.plain().strip()) > len(existing.plain().strip()):
                        existing.lines = list(cue.lines)
                        if index < len(self.translated):
                            self.translated[index] = ""
                            with self._cond:
                                if index not in self._pending_indices:
                                    self._pending_indices.append(index)
                                    self._cond.notify_all()
                    return False
        self.cues.append(cue)
        self.translated.append("")
        with self._cond:
            self._pending_indices.append(len(self.cues) - 1)
            self._cond.notify_all()
        return True

    # ------------------------------------------------------------ 翻译执行

    def _translator(self, live: bool = False) -> translate.Translator:
        return translate.build(self.config, live=live)

    def _translate_texts(self, texts: list[str], translator, target: str,
                         source_lang: str) -> list[str]:
        model = translator.model_label or translator.name
        engine_key = f"{translator.name}:{translate.PROMPT_VERSION}"
        keys = [self.cache.key(engine_key, model, target, t) for t in texts]
        results: list[str | None] = [None] * len(texts)
        miss_index: list[int] = []
        for index, key in enumerate(keys):
            cached = self.cache.get(key)
            if cached is not None and not translate.looks_untranslated(
                    texts[index], cached, source_lang, target):
                results[index] = cached
            else:
                miss_index.append(index)
        if miss_index:
            payload = [texts[i] for i in miss_index]
            attempts = 3
            last_error = None
            for attempt in range(attempts):
                try:
                    out = translator.translate(payload, target, source=source_lang or None)
                    if len(out) != len(payload):
                        raise translate.TranslationError(
                            f"返回 {len(out)} 条，期望 {len(payload)} 条")
                    echoes: list[int] = []
                    for index, value in zip(miss_index, out):
                        if translate.looks_untranslated(texts[index], value,
                                                        source_lang, target):
                            echoes.append(index)
                            results[index] = None      # 原样返回等于没翻，别写进缓存
                            continue
                        results[index] = value
                        self.cache.put(keys[index], value)
                    if echoes:
                        # 批量里偶尔有一两句模型偷懒；逐句再试一次通常能翻出来
                        retry_one = getattr(translator, "translate_one", None)
                        fixed = 0
                        if callable(retry_one):
                            for index in echoes:
                                try:
                                    one = retry_one(texts[index], target)
                                except Exception:
                                    continue
                                if one and not translate.looks_untranslated(
                                        texts[index], one, source_lang, target):
                                    results[index] = one
                                    self.cache.put(keys[index], one)
                                    fixed += 1
                        cfg.append_log(
                            f"翻译：{len(echoes)}/{len(payload)} 句模型原样返回，"
                            f"逐句补翻成功 {fixed} 句")
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    if self._cancel and self._cancel.is_set():
                        break
                    time.sleep(1.2 * (attempt + 1))
            if last_error:
                raise PipelineError(f"{translator.name} 翻译失败：{last_error}")
        return [r if r else texts[i] for i, r in enumerate(results)]

    def _blocks_near_playhead(self, block: int) -> list[list[int]]:
        playhead = self.engine.position or 0.0
        groups = [list(range(start, min(len(self.cues), start + block)))
                  for start in range(0, len(self.cues), block)]

        def score(indices: list[int]) -> float:
            begin = self.cues[indices[0]].start
            if begin >= playhead:
                return begin - playhead
            return (playhead - begin) * 6.0 + 1000.0

        groups.sort(key=score)
        return groups

    def _translate_all(self, cancel: threading.Event) -> None:
        if not self.cues:
            raise PipelineError("没有字幕可翻译")
        translator = self._translator()
        target = self.target
        source_lang = langtags.base_of(self.source.lang if self.source else "") or None
        total = len(self.cues)
        if len(self.translated) < total:
            self.translated.extend([""] * (total - len(self.translated)))
        # 旧译文里可能有“原文原样返回”的坏结果；先清掉再补翻，别让它冒充已完成
        for index, cue in enumerate(self.cues):
            text = self.translated[index]
            if text and translate.looks_untranslated(cue.plain(), text, source_lang, target):
                self.translated[index] = ""
        block = int(self.config.get("translate.batch_lines", 24))
        groups = self._blocks_near_playhead(block)
        if getattr(translator, "context_lines", 0) > 0:
            # 保留上下文时按时间顺序翻，保证上下文真的是“前文”
            groups.sort(key=lambda group: self.cues[group[0]].start)
        done = sum(1 for value in self.translated if value)
        self.progress.emit(
            f"翻译（{translate.engine_labels().get(translator.name, translator.name)}"
            f" → {cfg.lang_display(target)}）", done, total)
        workers = 3 if translator.name in ("openai", "deepl") else 1
        if getattr(translator, "context_lines", 0) > 0:
            workers = 1          # 保留上下文时必须按顺序翻，否则上下文会乱序
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {}
            for group in groups:
                if cancel.is_set():
                    return
                need = [index for index in group if not self.translated[index]]
                if not need:
                    continue
                texts = [self.cues[index].plain() for index in need]
                futures[pool.submit(self._translate_texts, texts, translator,
                                    target, source_lang)] = need
            for future in futures:
                if cancel.is_set():
                    return
                need = futures[future]
                try:
                    out = future.result()
                except PipelineError:
                    raise
                for index, value in zip(need, out):
                    self.translated[index] = value
                    with self._cond:
                        if index in self._pending_indices:
                            self._pending_indices.remove(index)
                done += len(need)
                self.progress.emit("翻译", min(done, total), total)
                self._render()
        self._render(force=True)
        if not self._live_session:
            # 原生字幕翻译完就是一份完整产物；AI 转写必须先有完成标记，
            # 否则把 16 句残片翻完再打上“完整”标记，下一轮又会被一键翻译复用
            origin = self.source.origin if self.source else "ai"
            if origin != "ai" or self.transcript_complete():
                self._write_marker(True)
        left = self.untranslated_count()
        target_name = cfg.lang_display(self.target)
        if not left:
            self.progress.emit("翻译完成", total, total)
        else:
            self.progress.emit(f"翻译完成 · {left} 句原样未翻",
                               max(0, total - left), total)
            self.error.emit(
                f"有 {left} 句模型原样返回、没有翻成{target_name}；"
                "建议在 AI 栏换 3B 或更大的翻译模型")
        extra = f" · {left} 句原样未翻" if left else ""
        self.notice.emit(f"翻译完成：{total} 条{extra} · 缓存命中 "
                         f"{self.cache.hits}/{self.cache.hits + self.cache.misses}")

    def _translate_existing(self, cancel: threading.Event) -> None:
        if self.source is None:
            raise PipelineError("还没选定要翻译的字幕轨")
        self._translate_all(cancel)

    # ------------------------------------------------------------ AI 字幕

    def language_hint(self) -> str:
        key = self.media_key or self.current_media
        if not key:
            return "auto"
        return cfg.media_language_hint(key)

    def set_language_hint(self, code: str) -> None:
        """只对当前这部片生效，避免给日语片选的「日语」把英语片也带偏。"""
        key = self.media_key or self.current_media
        if not key:
            return
        cfg.set_media_language_hint(key, code or "auto")
        self.whisper.clear_language_cache()
        self._lang_notice_key = None
        self._lang_log_key = None
        if self.is_busy() and not self._live_session:
            self.notice.emit("整片任务正在跑，识别语言更改会在下次任务生效")

    def audio_language(self) -> str:
        return langtags.base_of(self._audio_lang())

    def _asr_language(self) -> str:
        """选识别语言：本片指定 > 语音检测 > 音轨标签 > whisper 自动。

        音轨标签经常标错（WMA/WMV 的日语片常写成 eng）。只信标签会把日语
        直接送进 whisper 的英语模式，结果就是「日语被翻成英语」。
        """
        if not self.current_media:
            return "auto"
        hint = self.language_hint()
        tagged = self.audio_language()
        detected = ""
        if hint != "auto":
            chosen, reason = hint, "本片指定"
        else:
            near = self.cloud_live and self._live_session
            if near and getattr(self, "_live_lang_checked", False):
                detected = ""
            else:
                detected = self.whisper.detect_language(
                    self.current_media, self._audio_ordinal(),
                    headers=self.media_source.headers if self.media_source else None,
                    media_key=self.media_key,
                    display_name=self.media_source.display_name if self.media_source else None,
                    user_agent=self.media_source.user_agent if self.media_source else "",
                    at=(self.engine.position if near else None))
                if near:
                    self._live_lang_checked = True
            if detected:
                chosen, reason = detected, "语音检测"
            elif tagged:
                chosen, reason = tagged, "音轨标记"
            else:
                chosen, reason = "auto", "whisper 自动"
        log_key = (self.current_media, hint, tagged, detected, chosen)
        if log_key != self._lang_log_key:
            cfg.append_log(
                f"识别语言：本片={hint} · 音轨={tagged or '无'} · "
                f"检测={detected or '无'} → {chosen}（{reason}）")
            self._lang_log_key = log_key
        notice_key = (self.current_media, hint, tagged, chosen)
        if notice_key != self._lang_notice_key:
            self._lang_notice_key = notice_key
            if hint != "auto" and tagged and not langtags.same_lang(hint, tagged):
                self.notice.emit(
                    f"已按本片指定语言{cfg.lang_display(hint)}转写"
                    f"（音轨标记是{cfg.lang_display(tagged)}）")
            elif reason == "语音检测" and tagged \
                    and not langtags.same_lang(chosen, tagged):
                self.notice.emit(
                    f"音轨标记是{cfg.lang_display(tagged)}，"
                    f"语音检测为{cfg.lang_display(chosen)}，已按{cfg.lang_display(chosen)}转写")
        return chosen

    def generate_ai(self, live: bool = False, translate: bool = True,
                    released: bool = False, audio_index: int | None = None,
                    audio_lang: str = "", cloud_live: bool = False) -> None:
        ok, message = self.whisper.availability()
        if not ok:
            self.error.emit(message)
            return
        self.stop_all()
        self.cloud_live = bool(cloud_live)
        if released:
            self.player_released = True
            self.audio_index_override = audio_index
            self.audio_lang_override = audio_lang
        self._spawn(self._ai_worker, "AI 字幕", live, translate)

    def _ai_worker(self, cancel: threading.Event, live: bool, translate: bool) -> None:
        if not self.current_media:
            raise PipelineError("没有正在播放的文件")
        self.cues = []
        self.translated = []
        self.source = None
        self._transcript_verified = False
        self._asr_running.clear()
        self._asr_lang_hint = ""
        if live:
            # 实时字幕只用于本场播放：写临时文件、不落字幕库、不写 sidecar
            self._live_session = True
            self._live_lang_checked = False
            cfg.LIVE_DIR.mkdir(parents=True, exist_ok=True)
            self.live_output_path.unlink(missing_ok=True)
            self.output_path = self.live_output_path
            self.original_path = None
        else:
            self._live_session = False
            self._set_normal_output()
            # 先把旧标记置为「未完成」：这次中途停掉/失败，下次不能再把残片当整片
            self._write_marker(False, origin="ai")
        self.target = self.config.get("languages.translation_target", self.target)
        self.mode = self.config.get("languages.subtitle_display", self.mode)
        if not translate:
            self.mode = "original"
        translator = None
        if translate:
            if self.mode == "original":
                self.mode = "bilingual"     # 一键翻译要能看见译文
            try:
                translator = self._translator(live=live)
            except Exception as exc:
                # 本地转写不该被翻译密钥卡住：缺接口就降级成只出原文，whisper 照跑。
                # 早先这里是直接抛，用户点了「整片生成」只看到一句要密钥，转写根本没开始。
                self.notice.emit(f"没有可用的翻译接口（{exc}）：本轮只转写原文")
                self.mode = "original"
        if self.media_source is not None and self.media_source.is_remote:
            # 语言检测也会走 ffprobe/whisper；token 失效时先换一份再开始
            self._refresh_media_headers()
        lang = self._asr_language()
        self._asr_lang_hint = "" if lang == "auto" else lang
        writer_done = threading.Event()
        worker = None
        self._active_translator = translator
        if not live:
            # 转写阶段只让 ASR 的百分比占用任务条，翻译在后台照跑但不抢进度
            self._asr_running.set()
        if translator is not None:
            worker = threading.Thread(target=self._translation_consumer,
                                      args=(cancel, writer_done, translator, live),
                                      daemon=True, name="translate-consumer")
            worker.start()
        try:
            if live:
                self._live_loop(cancel, lang, writer_done)
            else:
                current_stage = {"text": "转写"}

                def on_cue(cue: Cue) -> None:
                    self._append_cue(cue)
                    self._render()      # 内部有 2 秒节流：整片转写也能边跑边出字

                def on_stage(text: str) -> None:
                    if text:
                        current_stage["text"] = text
                    self.notice.emit(text)

                def on_progress(ratio: float) -> None:
                    self.progress.emit(current_stage["text"], int(ratio * 100), 100)

                def run_asr() -> list[Cue]:
                    return self.whisper.transcribe_whole(
                        self.current_media, cancel, on_cue=on_cue,
                        on_progress=on_progress, stage=on_stage,
                        audio_index=self._audio_ordinal(), lang=lang,
                        headers=self.media_source.headers if self.media_source else None,
                        stem=self.media_stem,
                        display_name=self.media_source.display_name if self.media_source else None,
                        user_agent=self.media_source.user_agent if self.media_source else "")

                try:
                    cues = run_asr()
                except asr.AsrError as exc:
                    # 401/403 常见于 OpenList 重建过数据或换过 token；没有产出句子时
                    # 换 header 重跑一次不会重复字幕，已经出了句子就不冒险重来。
                    if self.cues or not _is_auth_error(exc) or not self._refresh_media_headers():
                        raise
                    cfg.append_log("云端鉴权失败，刷新 token 后重试一次转写")
                    cues = run_asr()
                if not self.cues and cues:
                    self._adopt_cues(cues)
                if not self.cues:
                    raise PipelineError(
                        "这段素材里没有识别到对白（可能是纯音乐/静音或选错了音轨）")
        except BaseException:
            # 转写失败/取消：别再把半截字幕继续翻译下去
            with self._cond:
                self._pending_indices.clear()
                self._cond.notify_all()
            writer_done.set()
            raise
        finally:
            if not live:
                self._asr_running.clear()
        self.source = SourceInfo(self.cues, lang if lang != "auto" else self.whisper.detected_language,
                                 "ai", self.output_path)
        self.languages_found.emit(self._audio_lang(), self.source.lang or "?")
        if not live:
            self._transcript_verified = True
            self._write_marker(True, origin="ai")
        self.asr_finished.emit()
        writer_done.set()
        with self._cond:
            self._cond.notify_all()
        if worker is not None:
            self.progress.emit("翻译", sum(1 for value in self.translated if value),
                               len(self.cues))
            # 等翻译真正跑完再结束任务；之前的 60 秒超时会让进度块提前消失，
            # 看起来像任务断了，但后台其实还在翻
            worker.join()
        else:
            with self._cond:
                self._pending_indices.clear()
        if not cancel.is_set():
            self._render(force=True)
            left = self.untranslated_count() if translator is not None else 0
            target_name = cfg.lang_display(self.target)
            if translator is not None:
                if left:
                    stage = f"转写完成 · 翻译有 {left} 句未完成"
                else:
                    stage = "转写 + 翻译完成"
                self.progress.emit(stage, max(0, len(self.cues) - left),
                                   len(self.cues))
            else:
                self.progress.emit("转写完成（未翻译）", len(self.cues), len(self.cues))
            suffix = " + 翻译" if translator is not None else "（未翻译）"
            extra = f"，其中 {left} 句没有翻成{target_name}" if left else ""
            self.notice.emit(
                f"AI 字幕就绪{suffix}：{len(self.cues)} 句 · "
                f"语言 {self.source.lang or '未知'}{extra}")
            if left:
                self.error.emit(
                    f"有 {left} 句模型原样返回、没有翻成{target_name}；"
                    "建议在 AI 栏换 3B 或更大的翻译模型")

    def translate_generated(self) -> None:
        """翻译已经生成的字幕：直接用内存里的原文，不重新跑 whisper。"""
        if not self.cues:
            self.error.emit("还没有可翻译的生成字幕，先点「转字幕」")
            return
        marker = self._read_marker()
        if marker and str(marker.get("origin") or "ai") == "ai" \
                and not self.transcript_complete():
            self.error.emit("这份 AI 转写是旧版/不完整（片头可能缺字幕），"
                            "请点「一键翻译」重新转写后再翻")
            return
        self.notice.emit(f"已有字幕 {len(self.cues)} 句，直接翻译（不重新转写）")
        self.target = self.config.get("languages.translation_target", self.target)
        if self.source is None:
            self.source = SourceInfo(list(self.cues), self.whisper.detected_language or "",
                                     "ai", self.output_path)
        if self.mode == "original":
            self.mode = "bilingual"
        if self.ai_pid is not None and self.engine.get("sid") != self.ai_pid:
            try:
                self.engine.set("sid", self.ai_pid)
                self.engine.reload_subtitle()
            except Exception:
                pass
        self._spawn(self._translate_existing, "翻译字幕")

    def _translation_consumer(self, cancel: threading.Event, done: threading.Event,
                              translator, live: bool = False) -> None:
        """独立线程吃新出现的句子，转写一边产出、翻译一边补，不必等全场跑完。"""
        if translator is None:
            return
        target = self.target
        # 实时模式一小批一小批翻，单批耗时短，译文更快追上画面
        batch_size = int(self.config.get(
            "translate.live_batch_lines" if live else "translate.batch_lines",
            8 if live else 24))
        batch_size = max(1, batch_size)
        retries: dict[tuple[int, ...], int] = {}
        cloud_cap = max(10, int(self.config.get("cloud.live_lead_max", 60) or 60))
        while not cancel.is_set():
            lead, unlimited = self._live_lead(cloud_cap)
            playhead = self.engine.position or 0.0
            horizon = None if (not live or unlimited) else playhead + lead
            with self._cond:
                if not self._pending_indices and not done.is_set():
                    self._cond.wait(0.6)
                eligible = [index for index in self._pending_indices
                            if horizon is None or self.cues[index].start <= horizon]
                batch = eligible[:batch_size]
                if batch:
                    for index in batch:
                        if index in self._pending_indices:
                            self._pending_indices.remove(index)
            if not batch:
                if done.is_set() and not self._pending_indices:
                    break
                # 字幕已经预读到了，但还在播放头前方窗口外：等播放头追上来
                with self._cond:
                    self._cond.wait(0.5)
                continue
            source = (self.source.lang if self.source is not None and self.source.lang
                      else self._asr_lang_hint or self.whisper.detected_language)
            source_lang = langtags.base_of(source) or None
            texts = [self.cues[i].plain() for i in batch]
            try:
                out = self._translate_texts(texts, translator, target, source_lang)
            except PipelineError as exc:
                self.error.emit(str(exc))
                if cancel.is_set():
                    return
                key = tuple(batch)
                attempts = retries.get(key, 0) + 1
                retries[key] = attempts
                if attempts <= 2:
                    # 这一批失败了先放回队尾，后面的句子继续翻，别整场停掉
                    with self._cond:
                        self._pending_indices.extend(batch)
                        self._cond.notify_all()
                time.sleep(0.8)
                continue
            if cancel.is_set():
                return
            retries.pop(tuple(batch), None)
            for index, value in zip(batch, out):
                self.translated[index] = value
            if not self._asr_running.is_set():
                self.progress.emit("翻译", sum(1 for v in self.translated if v),
                                   len(self.cues))
            self._render()
        if not cancel.is_set():
            self._render(force=True)

    def _prioritize_pending(self, position: float) -> None:
        """跳转后先翻播放头附近的句子，别让旧缓冲的待翻队列挡住新位置。"""
        translator = getattr(self, "_active_translator", None)
        clear = getattr(translator, "clear_context", None)
        if callable(clear):
            try:
                clear()          # 跳转后旧位置的上下文不再适用
            except Exception:
                pass
        with self._cond:
            self._pending_indices.sort(
                key=lambda index: abs(self.cues[index].start - position)
                if 0 <= index < len(self.cues) else 1e9)
            self._cond.notify_all()

    @staticmethod
    def _clamp_cues(cues: list[Cue], start: float, end: float) -> list[Cue]:
        """把 Whisper 的句子夹在当前音频窗口内，末尾留 20ms 防止盖住下一条。"""
        out: list[Cue] = []
        for cue in cues:
            cue_start = max(cue.start, start)
            cue_end = min(cue.end, end - 0.02)
            if cue_end - cue_start > 0.02:
                out.append(Cue(cue_start, cue_end, list(cue.lines)))
        return out

    def translated_count(self) -> int:
        """已经翻完几句 —— 面板要能回答「翻译到底还在不在动」。"""
        return sum(1 for text in self.translated if text)

    def _live_lead(self, cloud_cap: float) -> tuple[float, bool]:
        """这一轮实时允许超前播放头多少秒；返回 (秒, 是否不限超前)。

        云端还要再吃一层「缓存装得下的秒数」，否则 ASR 读点会把单流代理拽爆。
        """
        lead = max(0.0, float(self.config.get("asr.live_lead", 30.0)))
        if self.cloud_live:
            lead = min(lead, cloud_cap) if lead > 0 else cloud_cap
            if self.cloud_lead_cap > 0:
                lead = min(lead, max(10.0, float(self.cloud_lead_cap)))
        return lead, (lead <= 0 and not self.cloud_live)

    def _live_loop(self, cancel: threading.Event, lang: str, done: threading.Event) -> None:
        window = float(self.config.get("asr.window", 20.0))
        overlap = float(self.config.get("asr.overlap", 4.0))
        cloud_cap = max(10, int(self.config.get("cloud.live_lead_max", 60) or 60))
        lead, unlimited = self._live_lead(cloud_cap)
        last_end = 0.0
        last_position: float | None = None
        last_status = 0.0
        current_lang = lang or "auto"
        transient = 0
        mode_text = "整片（尽可能超前）" if unlimited else f"预读 {lead:.0f} 秒"
        if self.cloud_live:
            mode_text = f"云端单流 · 预读 {lead:.0f} 秒"
        self.notice.emit(f"实时模式已开启：{mode_text}，字幕/翻译尽量做在播放头前面")
        while not cancel.is_set():
            lead, unlimited = self._live_lead(cloud_cap)
            position = self.engine.position
            if position is None:
                time.sleep(0.3)
                continue
            resolved = self._asr_language()
            if resolved != current_lang:
                current_lang = resolved
                self._asr_lang_hint = "" if current_lang == "auto" else current_lang
                self._prioritize_pending(position)
                label = "自动" if current_lang == "auto" else cfg.lang_display(current_lang)
                self.notice.emit(f"识别语言已切换为{label}，下一段实时字幕生效")
            if last_position is not None and position < last_position - 1.5:
                # 往回拖进度条：旧缓冲在播放头后面了，从新位置重新预读
                last_end = max(0.0, position - 1.0)
                last_status = 0.0
                self._prioritize_pending(position)
                self.notice.emit("实时翻译：检测到回跳，从当前位置重新预读…")
            elif position > last_end + 15.0:     # 往前跳过了缓冲区，丢掉中间空档
                last_end = max(0.0, position - 1.0)
                last_status = 0.0
                self._prioritize_pending(position)
                self.notice.emit("实时翻译：检测到跳转，从当前位置重新预读…")
            last_position = position
            start = max(last_end - overlap, position - 0.5)
            duration = self.engine.duration
            if duration and start > duration - 0.5:
                self.notice.emit("实时字幕：已到片尾，停止转写")
                return
            if not unlimited and start > position + lead:   # 预读够了，等播放头追上来
                if time.time() - last_status > 2.0:
                    self.progress.emit(
                        f"实时翻译 · 预读 {lead:.0f}s · 已转 {len(self.cues)} 句"
                        f" · 已翻 {self.translated_count()} 句", len(self.cues), 0)
                    last_status = time.time()
                time.sleep(0.4)
                continue
            try:
                cues = self.whisper.transcribe_window(
                    self.current_media, start, window, cancel, lang=current_lang,
                    audio_index=self._audio_ordinal(),
                    headers=self.media_source.headers if self.media_source else None,
                    stem=self.media_stem,
                    display_name=self.media_source.display_name if self.media_source else None,
                    user_agent=self.media_source.user_agent if self.media_source else "")
            except asr.AsrError as exc:
                if "取消" in str(exc) or cancel.is_set():
                    return
                text = str(exc)
                broken = asr.is_stream_break(text)
                transient += 1
                # 代理自保暂停有 10 秒冷却，1/2/3 秒的短退避只会连着撞在同一个墙上；
                # 云端断流一律给到能跨过冷却的时间，再多给几次机会。
                limit = 10 if (broken or self.cloud_live) else 3
                if transient <= limit:
                    delay = min(30.0, (4.0 * transient) if (broken or self.cloud_live)
                                else float(transient))
                    cfg.append_log(
                        f"实时转写中断，{delay:.0f}s 后从当前位置重试（{transient}/{limit}）：{text}")
                    self.notice.emit(
                        f"实时翻译：{'音频流被掐断' if broken else '读取失败'}，"
                        f"{delay:.0f}s 后从当前位置重试（{transient}/{limit}）…")
                    waited = 0.0
                    while waited < delay and not cancel.is_set():
                        time.sleep(0.2)
                        waited += 0.2
                    if cancel.is_set():
                        return
                    pos = self.engine.position
                    if pos is not None:
                        last_end = max(0.0, pos - 1.0)
                        last_position = pos
                    continue
                if broken:
                    self.error.emit(
                        "实时翻译停了：115 的单流一直在被掐断（两条读流在抢同一条上游）。"
                        "把「网盘 → OpenList 服务」里的云端预读上限调到 30 秒、或把内存缓存调到 256 MB 再试；"
                        "长片也可以直接用一键翻译。")
                else:
                    self.error.emit(text)
                return
            transient = 0
            if cancel.is_set():
                return
            window_end = start + window
            if duration:
                window_end = min(window_end, duration)
            clamped = self._clamp_cues(cues or [], start, window_end)
            if clamped:
                for cue in clamped:
                    self._append_cue(cue)
                last_end = max(cue.end for cue in clamped)
                self.progress.emit("实时转写", len(self.cues), 0)
                # 不翻译时也要出字：渲染不能只挂在翻译线程上
                self._render()
            else:
                last_end = start + window * 0.92
            if done.is_set():
                return

    # ------------------------------------------------------------ 杂项

    def _audio_ordinal(self) -> int:
        """当前选定音轨在「内嵌音轨」里的序号，用来对齐 ffmpeg 的 0:a:N。

        只数内嵌轨：外挂音频文件不在容器流序号里，算进去会错位。
        """
        if self.audio_index_override is not None:
            return int(self.audio_index_override)
        internal = [t for t in self.engine.tracks_of("audio") if not t.get("external")]
        for index, track in enumerate(internal):
            if track.get("selected"):
                return index
        return 0

    def _audio_lang(self) -> str:
        if self.audio_lang_override:
            return self.audio_lang_override
        for track in self.engine.tracks_of("audio"):
            if track.get("selected"):
                return track.get("lang") or ""
        return ""

    def _spawn(self, runner, stage: str, *args) -> None:
        if self._cancel:
            self._cancel.set()
        cancel = threading.Event()
        self._cancel = cancel
        self._threads = [t for t in self._threads if t.is_alive()]

        def wrapper() -> None:
            self.busy.emit(True)
            started = time.time()
            cfg.append_log(f"{stage} 开始")
            try:
                runner(cancel, *args)
            except PipelineError as exc:
                if cancel.is_set():
                    cfg.append_log(f"{stage} 已停止")
                else:
                    cfg.append_log(f"{stage} 失败：{exc}")
                    self.error.emit(str(exc))
            except Exception as exc:
                if cancel.is_set():
                    cfg.append_log(f"{stage} 已停止")
                else:
                    detail = f"{type(exc).__name__}: {exc}"
                    cfg.append_log(f"{stage} 异常：{detail}")
                    self.error.emit(detail)
            else:
                cfg.append_log(f"{stage} 结束 · {time.time() - started:.0f}s")
            finally:
                self.busy.emit(False)

        thread = threading.Thread(target=wrapper, daemon=True, name=stage)
        self._threads.append(thread)
        thread.start()
