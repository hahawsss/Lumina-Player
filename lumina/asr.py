"""whisper.cpp 转写：整片按需 + 实时窗口流。

整片模式短视频一次喂完，长视频按 10 分钟分段并重叠 3 秒；边跑边从 stdout
收句子，进度按已处理时长映射回全片。实时模式只解码播放头之后的若干窗口
（默认 20 s，重叠 4 s 接住被切开的一句话）。两条路都靠 ffmpeg 抽 16 kHz
单声道 PCM；5.1/7.1 只取中置声道，避免对白被音乐盖住导致 whisper 复读。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import config as cfg
from .subtitles import Cue, split_long_cues

MODELS = {
    "ggml-tiny-q5_1.bin": ("tiny q5_1", "最快，约 75 MB，只适合听清普通话/英语的粗转写"),
    "ggml-base-q5_1.bin": ("base q5_1", "快，约 130 MB"),
    "ggml-small-q5_1.bin": ("small q5_1", "推荐，约 180 MB，中英质量够用"),
    "ggml-medium-q5_0.bin": ("medium q5_0", "慢，约 600 MB，明显更准"),
    "ggml-large-v3-turbo-q5_0.bin": ("large-v3-turbo", "最准，约 1.5 GB，M 系列上仍可实时"),
}
_MODEL_URLS = (
    "https://hf-mirror.com/ggerganov/whisper.cpp/resolve/main/{name}",
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{name}",
)

_SEGMENT = re.compile(
    r"\[(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\]\s*(.*)$"
)
# whisper-cli 新版只打百分比（`progress =  62%`），老版打时间比；两种都得认。
_PROGRESS = re.compile(
    r"progress\s*=\s*(?:(\d+):(\d+):(\d+)\.(\d+)\s*/\s*(\d+):(\d+):(\d+)\.(\d+)"
    r"|(\d+(?:\.\d+)?)\s*%)"
)
_LANGUAGE = re.compile(r"(?:detected|language)\s*(?:=|:)\s*'?([a-z]{2,3})'?", re.IGNORECASE)
_DETECT = re.compile(
    r"auto-detected language:\s*'?([a-z]{2,3})'?\s*\(p\s*=\s*([0-9.]+)\)", re.IGNORECASE)
_NOISE_WORDS = ("音楽", "音乐", "music", "bgm", "applause", "拍手", "silence",
                "無音", "无音", "沈黙", "blank_audio", "no speech", "噪声", "噪音")
# whisper 在音乐/静音上会凭空背出的固定句子（不是素材里的对白）
_HALLUCINATIONS = (
    "大阪市交通局8000形電車", "チャンネル登録", "subtitlesby", "amaraorg",
    "字幕由", "字幕提供",
)
_WHOLE_CHUNK = 600.0      # 整片超过 10 分钟就分段跑，别让一次 whisper 卡死整场
_WHOLE_OVERLAP = 3.0      # 段与段重叠，接住被切开的一句话；重复句由管线去重
_ZERO_RETRY_OFFSET = 30.0  # 某段 0 句时往后挪 30 s 再试：片头音乐常把 whisper 带进复读
TRANSCRIPT_VERSION = 2     # 转写算法版本；旧版残留没有这个标记，一键翻译会重新转写


class AsrError(RuntimeError):
    pass


def _progress_ratio(line: str) -> float | None:
    match = _PROGRESS.search(line)
    if not match:
        return None
    if match.group(9) is not None:
        return max(0.0, min(1.0, float(match.group(9)) / 100.0))
    done = (int(match.group(1)) * 3600 + int(match.group(2)) * 60 + int(match.group(3))
            + float("0." + match.group(4)))
    total = (int(match.group(5)) * 3600 + int(match.group(6)) * 60 + int(match.group(7))
             + float("0." + match.group(8)))
    return max(0.0, min(1.0, done / total)) if total > 0 else None


def _norm_noise(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text).lower()


_NOISE_NORM = tuple(_norm_noise(word) for word in _NOISE_WORDS)
_HALLU_NORM = tuple(_norm_noise(word) for word in _HALLUCINATIONS)


def _is_noise(text: str) -> bool:
    """丢掉 `(音楽)` / `[BLANK_AUDIO]` 这类非对白，别让它占一条字幕。"""
    cleaned = (text or "").strip()
    if not cleaned:
        return True
    if re.fullmatch(r"[♪♫♬♩\s·.…\-—]+", cleaned):
        return True
    # 整句都是括号/方括号里的音效说明（(小声)、(鳴き声)、[DOOR OPENING]…），
    # 不是对白；只保留括号和真正台词混在一起的句子。
    if re.fullmatch(r"[\(\[（【][^\)\]）】]{1,40}[\)\]）】][。.!?！？]?", cleaned):
        return True
    norm = _norm_noise(cleaned)
    if norm in _NOISE_NORM:
        return True
    return any(word in norm for word in _HALLU_NORM)


def _header_text(headers: dict[str, str] | None) -> str:
    if not headers:
        return ""
    return "".join(f"{name}: {value}\r\n" for name, value in headers.items())


def media_duration(media: str, headers: dict[str, str] | None = None,
                   user_agent: str = "") -> float:
    try:
        args = [find_binary("ffprobe"), "-v", "error"]
        if headers:
            args += ["-headers", _header_text(headers)]
        if user_agent:
            args += ["-user_agent", user_agent]
        args += ["-show_entries", "format=duration", "-of", "default=nw=1:nk=1",
                 str(media)]
        proc = subprocess.run(args, capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            return 0.0
        first = (proc.stdout or "").strip().splitlines()
        return max(0.0, float(first[0])) if first else 0.0
    except (AsrError, OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return 0.0


def _clock(seconds: float) -> str:
    total = int(max(0.0, seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def find_binary(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    for prefix in ("/opt/homebrew/bin", "/usr/local/bin"):
        candidate = Path(prefix) / name
        if candidate.is_file():
            return str(candidate)
    raise AsrError(f"找不到 {name}，请先安装：brew install whisper-cpp ffmpeg")


_STREAM_BREAK = ("Stream ends prematurely", "Error during demuxing",
                 "Input/output error", "Connection reset", "Connection timed out",
                 "Server returned 5", "Server returned 429", "Invalid data found",
                 "End of file")


def is_stream_break(detail: str) -> bool:
    """ffmpeg 读到一半被掐断的样子：这是上游断了可以重试，不是素材没音轨。"""
    text = detail or ""
    return any(marker in text for marker in _STREAM_BREAK)


def _supported_flags(binary: str) -> set[str]:
    try:
        proc = subprocess.run([binary, "--help"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return set()
    names: set[str] = set()
    for line in (proc.stdout + proc.stderr).splitlines():
        for token in re.findall(r"(?:^|\s)-{1,2}([A-Za-z][A-Za-z0-9-]*)", line):
            names.add(token)
    return names


def list_models() -> list[str]:
    if not cfg.ASR_MODEL_DIR.is_dir():
        return []
    return sorted(p.name for p in cfg.ASR_MODEL_DIR.glob("ggml-*.bin"))


_AVAIL: dict[str, tuple[float, bool, str]] = {}


def availability_cached(config: cfg.Config, ttl: float = 300.0) -> tuple[bool, str]:
    """whisper --help 要半秒多（Metal 初始化），别在 GUI 线程上反复问。"""
    key = availability_key(config)
    hit = _AVAIL.get(key)
    now = time.time()
    if hit and now - hit[0] < ttl:
        return hit[1], hit[2]
    ok, message = Whisper(config).availability()
    _AVAIL[key] = (now, ok, message)
    return ok, message


def availability_key(config: cfg.Config) -> str:
    return f"{config.get('asr.binary')}|{config.get('asr.model')}"


def availability_now(config: cfg.Config) -> tuple[bool, str] | None:
    """只读缓存，绝不在这上面跑子进程——给 GUI 线程用。"""
    hit = _AVAIL.get(availability_key(config))
    if not hit or time.time() - hit[0] > 300:
        return None
    return hit[1], hit[2]


def warm_async(config: cfg.Config) -> None:
    threading.Thread(target=lambda: availability_cached(config), daemon=True).start()


def download_model(name: str, on_progress=None, cancel: threading.Event | None = None,
                   on_note=None) -> Path:
    from .translate import download_from_mirrors
    dest = cfg.ASR_MODEL_DIR / name
    cfg.ensure_dirs()
    if dest.exists() and dest.stat().st_size > 1_000_000:
        return dest
    urls = [template.format(name=name) for template in _MODEL_URLS]
    return download_from_mirrors(urls, dest, on_progress, cancel,
                                 error_cls=AsrError, on_note=on_note,
                                 chunk=1 << 16)


class Whisper:
    def __init__(self, config: cfg.Config):
        self.config = config
        self.binary = ""
        self.ffmpeg = ""
        self.ffprobe = ""
        self._flags: set[str] = set()
        self._audio_profiles: dict[tuple, tuple[int, str]] = {}
        self._lang_detect_cache: dict[tuple, str] = {}
        self.detected_language: str = ""

    def clear_language_cache(self) -> None:
        """用户改识别语言后让下一次检测重新抽样，别复用旧的失败结果。"""
        self._lang_detect_cache.clear()

    # ------------------------------------------------------------ 自检

    def prepare(self) -> None:
        if self.binary:
            return
        self.binary = find_binary(self.config.get("asr.binary", "whisper-cli"))
        self.ffmpeg = find_binary("ffmpeg")
        self.ffprobe = find_binary("ffprobe")
        self._flags = _supported_flags(self.binary)

    def _flag(self, name: str, value=None) -> list[str]:
        if self._flags and name not in self._flags:
            return []
        return [f"--{name}"] + ([str(value)] if value is not None else [])

    def availability(self) -> tuple[bool, str]:
        try:
            self.prepare()
        except AsrError as exc:
            return False, str(exc)
        model = self.model_path()
        if model is None:
            return False, f"缺少模型 {self.config.get('asr.model')}，在设置里下载"
        return True, f"{Path(self.binary).name} · {model.name} · Metal 加速"

    def model_path(self) -> Path | None:
        wanted = self.config.get("asr.model", "ggml-small-q5_1.bin")
        direct = cfg.ASR_MODEL_DIR / wanted
        if direct.is_file():
            return direct
        found = list_models()
        return Path(cfg.ASR_MODEL_DIR / found[0]) if found else None

    # ------------------------------------------------------------ 取音频

    def _audio_profile(self, media: str, audio_index: int,
                       headers: dict[str, str] | None = None,
                       user_agent: str = "") -> tuple[int, str]:
        key = (str(media), int(audio_index),
               tuple(sorted((headers or {}).items())), user_agent)
        if key in self._audio_profiles:
            return self._audio_profiles[key]
        profile = (0, "")
        try:
            args = [self.ffprobe, "-v", "error"]
            if headers:
                args += ["-headers", _header_text(headers)]
            if user_agent:
                args += ["-user_agent", user_agent]
            args += ["-select_streams", f"a:{max(0, int(audio_index))}",
                     "-show_entries", "stream=channels,channel_layout",
                     "-of", "json", str(media)]
            proc = subprocess.run(args, capture_output=True, text=True, timeout=30)
            streams = (json.loads(proc.stdout or "{}").get("streams") or []) \
                if proc.returncode == 0 else []
            if streams:
                profile = (int(streams[0].get("channels") or 0),
                           str(streams[0].get("channel_layout") or ""))
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        self._audio_profiles[key] = profile
        return profile

    @staticmethod
    def language_probe_points(duration: float) -> list[float]:
        """语种检测的采样点：先试片头 5/10/20 分钟，再按比例补点。

        只按 10%/50%/90% 取点会踩在音乐/静音段落上（实测一部日语片三个点
        都低于 0.6，随后 whisper auto 把日语转成了英语）。片头往往有对白，
        先试绝对时间，命中高置信度就停。
        """
        duration = max(0.0, float(duration))
        points: list[float] = []
        for absolute in (300.0, 600.0, 1200.0):
            if duration >= absolute + 40:
                points.append(absolute)
        for fraction in (0.05, 0.1, 0.2, 0.35, 0.5, 0.7, 0.9):
            t0 = min(max(15.0, duration * fraction), max(0.0, duration - 10.0))
            points.append(round(t0, 1))
        points.sort()
        unique: list[float] = []
        for t0 in points:
            if not unique or t0 - unique[-1] >= 60.0:
                unique.append(t0)
        return unique if duration >= 8.0 else []

    def detect_language(self, media: str, audio_index: int = 0,
                        headers: dict[str, str] | None = None,
                        media_key: str | None = None,
                        display_name: str | None = None,
                        user_agent: str = "", at: float | None = None) -> str:
        """在多个时间点取 30 秒做语种检测，置信度不够时做投票。

        音轨的 language 标签经常是错的（日语成人片常被标成 eng），
        直接按标签跑 whisper 会把日语「翻译」成英语；这里用真实语音纠正。
        """
        self.prepare()
        if self._flags and "detect-language" not in self._flags:
            return ""
        duration = media_duration(media, headers, user_agent)
        if duration < 8.0:
            return ""
        try:
            stat = Path(media).stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            stamp = (0, 0)
        key = (media_key or str(media), int(audio_index), stamp,
               None if at is None else round(float(at), 1))
        if key in self._lang_detect_cache:
            return self._lang_detect_cache[key]
        model = self.model_path()
        if model is None:
            return ""
        name = display_name or Path(media).name
        stem = cfg.safe_name(Path(name).stem)
        samples: list[tuple[str, float, float]] = []
        best_lang, best_p, best_at = "", 0.0, 0.0
        points = self.language_probe_points(duration)
        if at is not None:
            # 云端实时：只抽播放头附近，别为了检测把单流上游拉去几十万字节外
            near = min(max(0.0, float(at)), max(0.0, duration - 10.0))
            points = [round(near, 1)]
        for t0 in points:
            length = min(30.0, duration - t0)
            if length < 5.0:
                continue
            wav = cfg.WORK_DIR / f"{stem}-lang-{int(t0)}.wav"
            try:
                self.extract_audio(media, wav, t0=t0, duration=length,
                                   audio_index=audio_index, headers=headers,
                                   user_agent=user_agent)
                proc = subprocess.run(
                    [self.binary, "--model", str(model), "--file", str(wav),
                     "--language", "auto", *self._flag("detect-language")],
                    capture_output=True, text=True, timeout=90,
                )
                text = f"{proc.stdout or ''}\n{proc.stderr or ''}"
                found_lang, found_p = "", 0.0
                for match in _DETECT.finditer(text):
                    language, probability = match.group(1), float(match.group(2))
                    if probability > found_p:
                        found_lang, found_p = language, probability
                if found_lang:
                    samples.append((found_lang, found_p, t0))
                    cfg.append_log(
                        f"语种采样：{name} · {_clock(t0)} → {found_lang}（p={found_p:.2f}）")
                    if found_p > best_p:
                        best_lang, best_p, best_at = found_lang, found_p, t0
                    if found_p >= 0.8:
                        break
                else:
                    cfg.append_log(f"语种采样：{name} · {_clock(t0)} → 无结果")
            except (AsrError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
                cfg.append_log(
                    f"语种采样：{name} · {_clock(t0)} → 失败：{str(exc)[:100]}")
                continue
            finally:
                wav.unlink(missing_ok=True)
        result = best_lang if best_p >= 0.65 else ""
        if not result and samples:
            # 没有一个点够 0.65 时，要求同一种语言至少出现两次且均值够
            votes: dict[str, list[float]] = {}
            for language, probability, at in samples:
                if probability >= 0.45:
                    votes.setdefault(language, []).append(probability)
            if votes:
                language, values = max(votes.items(),
                                       key=lambda item: (len(item[1]), sum(item[1])))
                mean = sum(values) / len(values)
                if len(values) >= 2 and mean >= 0.5:
                    result, best_p = language, mean
                    best_at = next(at for lang, _, at in samples if lang == language)
        self._lang_detect_cache[key] = result
        cfg.append_log(
            f"语种检测：{name} · 采样 {_clock(best_at)} · "
            f"{result or '失败'}（p={best_p:.2f}）")
        return result

    def extract_audio(self, media: str, out_wav: Path, t0: float | None = None,
                      duration: float | None = None, cancel: threading.Event | None = None,
                      audio_index: int = 0,
                      headers: dict[str, str] | None = None,
                      user_agent: str = "") -> Path:
        self.prepare()
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        args = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
        if t0 is not None:
            args += ["-ss", f"{max(0.0, t0):.3f}"]
        if headers:
            args += ["-headers", _header_text(headers)]
        if user_agent:
            args += ["-user_agent", user_agent]
        args += ["-i", str(media)]
        if duration is not None:
            args += ["-t", f"{duration:.3f}"]
        args += ["-vn", "-map", f"0:a:{max(0, int(audio_index))}"]
        channels, _layout = self._audio_profile(
            media, audio_index, headers=headers, user_agent=user_agent)
        if channels >= 6:
            # 5.1/7.1 对白几乎都在中置；整轨下混会被音乐/环绕声盖住，
            # whisper 会陷入复读（同一句每 2 秒重复几十遍）。只取中置。
            args += ["-af", "pan=mono|c0=c2"]
        else:
            args += ["-ac", "1"]
        args += ["-ar", "16000", "-c:a", "pcm_s16le", "-f", "wav", str(out_wav)]
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if cancel is not None:
            while proc.poll() is None:
                if cancel.is_set():
                    proc.terminate()
                    break
                time.sleep(0.1)
        stderr = proc.stderr.read() if proc.stderr else ""
        if proc.poll() == 143 or (cancel and cancel.is_set()):
            raise AsrError("已取消")
        if proc.returncode != 0:
            raise AsrError(f"抽取音频失败: {stderr.strip()[:300]}")
        if not out_wav.exists() or out_wav.stat().st_size < 1000:
            detail = (stderr or "").strip()[-300:] or "输出为空"
            cfg.append_log(f"抽取音频失败：{media} · {detail}")
            if is_stream_break(detail):
                raise AsrError(f"音频流读取被掐断：{detail}")
            raise AsrError(f"这条素材里没有可转写的音轨：{detail}")
        return out_wav

    # ------------------------------------------------------------ 跑模型

    def _run(self, wav: Path, lang: str, cancel: threading.Event,
             on_cue=None, on_progress=None, beam: int = 5, log: bool = False) -> list[Cue]:
        self.prepare()
        model = self.model_path()
        if model is None:
            raise AsrError("没有可用的 whisper 模型")
        threads = int(self.config.get("asr.threads", 0) or 0) or (4 if beam <= 1 else 0)
        args = [self.binary, "--model", str(model), "--file", str(wav),
                "--language", (lang or "auto")]
        if threads:
            args += self._flag("threads", threads)
        if beam == 1:
            args += self._flag("beam-size", 1)
        args += self._flag("print-progress")
        started = time.time()
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1, encoding="utf-8", errors="replace")
        limit = int(self.config.get("asr.max_chars", 44))
        cues: list[Cue] = []
        pending: Cue | None = None
        last_ratio = 0.0
        saw_progress = False
        last_norm = ""
        last_end = -1e9
        repeat_count = 0
        assert proc.stdout is not None

        def emit(cue: Cue) -> None:
            """逐句回调也要经过切块，否则实时产出的字幕仍是整段长句。"""
            nonlocal last_norm, last_end, repeat_count
            pieces = [piece for piece in split_long_cues([cue], limit=limit)
                      if not _is_noise(piece.plain())]
            kept: list[Cue] = []
            for piece in pieces:
                norm = re.sub(r"[\s\W_]+", "", piece.plain()).lower()
                if norm and norm == last_norm and piece.start - last_end <= 3.0:
                    repeat_count += 1
                else:
                    last_norm = norm
                    repeat_count = 1
                last_end = max(last_end, piece.end)
                # whisper 在音乐/混响段落会复读同一句；留前 6 条，别刷满整场
                if repeat_count <= 6:
                    kept.append(piece)
            pieces = kept
            cues.extend(pieces)
            if on_cue:
                for piece in pieces:
                    on_cue(piece)

        for line in proc.stdout:
            if cancel.is_set():
                proc.terminate()
                break
            line = line.rstrip("\n")
            match = _SEGMENT.search(line)
            if match:
                h1, m1, s1, f1, h2, m2, s2, f2, text = match.groups()
                start = int(h1) * 3600 + int(m1) * 60 + int(s1) + int(f1) / 1000.0
                end = int(h2) * 3600 + int(m2) * 60 + int(s2) + int(f2) / 1000.0
                if pending is not None and pending.lines:
                    emit(pending)
                pending = None
                cleaned = text.strip()
                if cleaned:
                    emit(Cue(start, max(end, start + 0.3), [cleaned]))
                else:
                    pending = Cue(start, max(end, start + 0.3), [])
                continue
            if pending is not None and line.strip():
                pending.lines.append(line.strip())
                continue
            ratio = _progress_ratio(line)
            if ratio is not None:
                last_ratio = ratio
                saw_progress = True
                if on_progress:
                    on_progress(ratio)
            if not self.detected_language:
                lang_match = _LANGUAGE.search(line)
                if lang_match:
                    self.detected_language = lang_match.group(1)
        if pending is not None and pending.lines:
            emit(pending)
        returncode = proc.wait()
        elapsed = time.time() - started
        where = Path(wav).name
        if cancel.is_set():
            if log:
                cfg.append_log(f"whisper 已取消：{where} · 已生成 {len(cues)} 句 · {elapsed:.0f}s")
            raise AsrError("已取消")
        if returncode not in (0, None):
            detail = (f"whisper 提前退出（rc={returncode}，{where}），"
                      f"只生成 {len(cues)} 句，字幕不完整")
            cfg.append_log(detail)
            raise AsrError(detail)
        if saw_progress and last_ratio < 0.9:
            detail = (f"whisper 只处理到 {last_ratio:.0%} 就退出了（{where}），"
                      f"只生成 {len(cues)} 句，字幕不完整")
            cfg.append_log(detail)
            raise AsrError(detail)
        if log:
            cfg.append_log(
                f"whisper 完成：{where} · {len(cues)} 句 · 进度 {last_ratio:.0%} · {elapsed:.0f}s")
        return cues

    @staticmethod
    def chunk_windows(duration: float, chunk: float = _WHOLE_CHUNK,
                      overlap: float = _WHOLE_OVERLAP) -> list[tuple[float, float]]:
        """把整片切成 [(起点, 时长)]：每段比步长多 overlap 秒，接住被切开的话。"""
        duration = max(0.0, float(duration))
        step = max(1.0, float(chunk))
        pad = max(0.0, float(overlap))
        windows: list[tuple[float, float]] = []
        start = 0.0
        while start < duration - 0.05:
            end = min(duration, start + step + pad)
            windows.append((round(start, 3), round(max(0.0, end - start), 3)))
            if end >= duration - 0.05:
                break
            start += step
        return windows

    def transcribe_whole(self, media: str, cancel: threading.Event, on_cue=None,
                         on_progress=None, stage=None, audio_index: int = 0,
                         lang: str | None = None,
                         headers: dict[str, str] | None = None,
                         stem: str | None = None,
                         display_name: str | None = None,
                         user_agent: str = "") -> list[Cue]:
        """整片转写：短视频一次喂完，长视频按 10 分钟分段，进度与句子都持续回调。"""
        self.prepare()
        duration = media_duration(media, headers, user_agent)
        self.detected_language = ""
        lang = lang or str(self.config.get("asr.language", "auto") or "auto")
        if duration > _WHOLE_CHUNK + _WHOLE_OVERLAP:
            return self._transcribe_chunked(media, duration, lang, cancel, on_cue,
                                            on_progress, stage, audio_index,
                                            headers=headers, stem=stem,
                                            display_name=display_name,
                                            user_agent=user_agent)
        name = display_name or Path(media).name
        stem = stem or cfg.safe_name(Path(name).stem)
        wav = cfg.WORK_DIR / f"{stem}-16k.wav"
        cfg.append_log(f"整片转写开始：{name} · {duration / 60:.1f} 分钟 · 语言={lang}")
        started = time.time()
        if stage:
            stage("抽取音频…")
        try:
            self.extract_audio(media, wav, cancel=cancel, audio_index=audio_index,
                               headers=headers, user_agent=user_agent)
            if stage:
                stage("转写中…")
            cues = self._run(wav, lang, cancel, on_cue=on_cue, on_progress=on_progress,
                             log=True)
        finally:
            wav.unlink(missing_ok=True)
        cfg.append_log(
            f"整片转写完成：{name} · {len(cues)} 句 · {time.time() - started:.0f}s")
        return cues

    def _transcribe_chunked(self, media: str, duration: float, lang: str,
                            cancel: threading.Event, on_cue=None, on_progress=None,
                            stage=None, audio_index: int = 0,
                            headers: dict[str, str] | None = None,
                            stem: str | None = None,
                            display_name: str | None = None,
                            user_agent: str = "") -> list[Cue]:
        windows = self.chunk_windows(duration)
        total = len(windows)
        name = display_name or Path(media).name
        stem = stem or cfg.safe_name(Path(name).stem)
        all_cues: list[Cue] = []
        last_overall = 0.0
        run_lang = lang or "auto"
        started = time.time()
        cfg.append_log(
            f"分段转写开始：{name} · {duration / 60:.1f} 分钟 · "
            f"{total} 段 · 语言={run_lang}")

        for index, (t0, length) in enumerate(windows):
            if cancel.is_set():
                raise AsrError("已取消")
            chunk_end = min(duration, t0 + length)
            label = f"第 {index + 1}/{total} 段（{_clock(t0)}–{_clock(chunk_end)}）"
            wav = cfg.WORK_DIR / f"{stem}-part-{int(t0)}.wav"
            if stage:
                stage(f"{label} · 抽取音频…")
            try:
                self.extract_audio(media, wav, t0=t0, duration=length, cancel=cancel,
                                   audio_index=audio_index, headers=headers,
                                   user_agent=user_agent)
            except AsrError as exc:
                if cancel.is_set() or "取消" in str(exc):
                    raise AsrError("已取消") from exc
                raise AsrError(f"{label} 抽取音频失败：{exc}") from exc

            chunk_cues: list[Cue] = []

            def shift(cue: Cue, t0: float = t0, chunk_end: float = chunk_end) -> None:
                begin = max(t0, t0 + cue.start)
                end = min(chunk_end - 0.02, t0 + cue.end)
                if end - begin < 0.12:
                    return
                shifted = Cue(begin, max(begin + 0.2, end), list(cue.lines))
                chunk_cues.append(shifted)
                all_cues.append(shifted)
                if on_cue:
                    on_cue(shifted)

            def chunk_progress(ratio: float, t0: float = t0, length: float = length) -> None:
                nonlocal last_overall
                overall = min(1.0, (t0 + length * max(0.0, min(1.0, ratio))) / duration)
                if overall > last_overall:
                    last_overall = overall
                if on_progress:
                    on_progress(last_overall)

            if stage:
                stage(f"{label} · 转写中…")
            try:
                for attempt in range(2):
                    if cancel.is_set():
                        raise AsrError("已取消")
                    try:
                        self._run(wav, run_lang, cancel, on_cue=shift,
                                  on_progress=chunk_progress, log=True)
                        break
                    except AsrError as exc:
                        if cancel.is_set() or "取消" in str(exc):
                            raise
                        if attempt == 0:
                            cfg.append_log(f"{label} 转写失败，重试：{exc}")
                            if stage:
                                stage(f"{label} · 重试…")
                            time.sleep(1.0)
                            continue
                        raise AsrError(f"{label} 转写失败：{exc}") from exc
            finally:
                wav.unlink(missing_ok=True)

            if not chunk_cues and length > _ZERO_RETRY_OFFSET + 10:
                # 片头/音乐段会让 whisper 从 0 开始就陷入复读；从 30 s 起再跑一遍，
                # 往往能接回后面的正常对白。首轮已经是 0 句，不会有重复。
                offset = _ZERO_RETRY_OFFSET
                rt0 = t0 + offset
                rlength = chunk_end - rt0
                rwav = cfg.WORK_DIR / f"{stem}-part-{int(t0)}-off{int(offset)}.wav"
                if stage:
                    stage(f"{label} · 首轮 0 句，偏移 {offset:.0f}s 重试…")
                cfg.append_log(f"{label} 首轮 0 句，从 {_clock(rt0)} 起重试")
                try:
                    self.extract_audio(media, rwav, t0=rt0, duration=rlength,
                                       cancel=cancel, audio_index=audio_index,
                                       headers=headers, user_agent=user_agent)

                    def shift_retry(cue: Cue, rt0: float = rt0,
                                    chunk_end: float = chunk_end) -> None:
                        begin = max(rt0, rt0 + cue.start)
                        end = min(chunk_end - 0.02, rt0 + cue.end)
                        if end - begin < 0.12:
                            return
                        shifted = Cue(begin, max(begin + 0.2, end), list(cue.lines))
                        chunk_cues.append(shifted)
                        all_cues.append(shifted)
                        if on_cue:
                            on_cue(shifted)

                    self._run(rwav, run_lang, cancel, on_cue=shift_retry, log=True)
                except AsrError as exc:
                    if cancel.is_set() or "取消" in str(exc):
                        raise AsrError("已取消") from exc
                    cfg.append_log(f"{label} 偏移重试失败（保留首轮结果）：{exc}")
                finally:
                    rwav.unlink(missing_ok=True)

            chunk_progress(1.0)
            if run_lang in ("", "auto") and self.detected_language:
                run_lang = self.detected_language

        if on_progress:
            on_progress(1.0)
        cfg.append_log(
            f"分段转写完成：{name} · {total} 段 · {len(all_cues)} 句 · "
            f"{time.time() - started:.0f}s · 语言={self.detected_language or run_lang}")
        return all_cues

    def transcribe_window(self, media: str, t0: float, length: float,
                          cancel: threading.Event, lang: str = "auto",
                          audio_index: int = 0,
                          headers: dict[str, str] | None = None,
                          stem: str | None = None,
                          display_name: str | None = None,
                          user_agent: str = "") -> list[Cue]:
        """只转写 [t0, t0+length] 这一段，时间戳已平移回原片坐标。"""
        self.prepare()
        stem = stem or cfg.safe_name(Path(display_name or media).stem)
        wav = cfg.WORK_DIR / f"{stem}-win-{int(t0)}.wav"
        try:
            self.extract_audio(media, wav, t0=t0, duration=length, cancel=cancel,
                               audio_index=audio_index, headers=headers,
                               user_agent=user_agent)
            cues = self._run(wav, lang, cancel, beam=1)
            for cue in cues:
                cue.start += t0
                cue.end += t0
            return cues
        finally:
            wav.unlink(missing_ok=True)
