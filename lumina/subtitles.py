"""字幕解析与生成：SRT / VTT / ASS 读入，ASS 写出（含双语两行）。

翻译需要的是纯文本，所以解析时统一剥掉 ASS override 块和 HTML 标签；
渲染交回 mpv，我们只负责生成一个带自己样式的 ASS 文件。
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

_SRT_TIME = re.compile(
    r"(?:(\d+):)?(\d{1,2}):(\d{1,2})[,.](\d{1,3})"
)
_ASS_OVERRIDE = re.compile(r"\{[^}]*\}")
_HTML_TAG = re.compile(r"<\s*/?\s*[^>]*>")
_ASS_FIELD = re.compile(r"^Dialogue:\s*(?P<fields>.*)$", re.IGNORECASE)

SUB_FIX = 1000  # ASS 时间单位：毫秒写进 1/1000 秒


@dataclass
class Cue:
    start: float
    end: float
    lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def plain(self) -> str:
        return self.text.strip()

    def is_empty(self) -> bool:
        return not self.plain() or bool(re.fullmatch(r"[-—.、,，.]+\s*", self.plain()))


def parse_time(spec: str) -> float | None:
    """接受 00:01:02,345 / 1:02.34 / 01:02:03.45 等写法。"""
    match = _SRT_TIME.search(spec.strip())
    if not match:
        return None
    hours = int(match.group(1) or 0)
    return hours * 3600 + int(match.group(2)) * 60 + int(match.group(3)) + float("0." + match.group(4))


def format_time_srt(seconds: float) -> str:
    seconds = max(0.0, seconds)
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def format_time_ass(seconds: float) -> str:
    seconds = max(0.0, seconds)
    cs = int(round(seconds * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def clean_text(raw: str) -> str:
    """剥样式，保留换行结构。"""
    text = _ASS_OVERRIDE.sub("", raw)
    text = text.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
    text = re.sub(r"\\[a-zA-Z][a-zA-Z0-9]*[^\\;]*[;]?", "", text)
    text = _HTML_TAG.sub("", text)
    text = html.unescape(text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join([line for line in lines if line])


def parse_srt(text: str) -> list[Cue]:
    cues: list[Cue] = []
    block_pat = re.split(r"\r?\n\s*\r?\n", text.replace("\r\n", "\n").replace("\ufeff", ""))
    for block in block_pat:
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        if "-->" not in "".join(lines[:2]):
            continue
        timing_index = 0
        for index, line in enumerate(lines[:2]):
            if "-->" in line:
                timing_index = index
                break
        parts = lines[timing_index].split("-->")
        if len(parts) != 2:
            continue
        start, end = parse_time(parts[0]), parse_time(parts[1])
        if start is None or end is None:
            continue
        body = "\n".join(lines[timing_index + 1:])
        cue = Cue(start, max(end, start + 0.2), [ln for ln in clean_text(body).splitlines() if ln])
        if not cue.is_empty():
            cues.append(cue)
    return cues


def parse_vtt(text: str) -> list[Cue]:
    cleaned = text.replace("\ufeff", "").replace("\r\n", "\n")
    cleaned = re.sub(r"^WEBVTT[^\n]*\n", "", cleaned)
    cleaned = re.sub(r"\nNOTE[^\n]*\n(?:[^\n]|\n(?!\n))*", "\n", cleaned)
    cleaned = re.sub(r"style:[^\n]*\n", "", cleaned)
    return parse_srt(cleaned)


def parse_ass(text: str) -> list[Cue]:
    # Format 行在 Styles 与 Events 段各有一条，只认 Events 段里的那条
    events = re.search(r"\[Events\](.*)", text, flags=re.DOTALL)
    segment = events.group(1) if events else text
    header = re.search(r"Format:\s*(?P<fmt>[^\n]*)", segment)
    fields = [f.strip().lower() for f in header.group("fmt").split(",")] if header else []
    if "start" not in fields or "text" not in fields:
        fields = ["layer", "start", "end", "style", "name",
                  "marginl", "marginr", "marginv", "effect", "text"]
    text_index = fields.index("text")
    start_index = fields.index("start")
    end_index = fields.index("end")

    def ass_to_seconds(value: str) -> float | None:
        parts = value.strip().split(":")
        if len(parts) != 3:
            return None
        try:
            h, m = int(parts[0]), int(parts[1])
            s, cs = (parts[2].split(".") + ["0"])[:2]
            return h * 3600 + m * 60 + int(s) + float("0." + cs) / 100.0
        except ValueError:
            return None

    cues: list[Cue] = []
    for line in text.splitlines():
        match = _ASS_FIELD.match(line.strip())
        if not match:
            continue
        parts = match.group("fields").split(",", text_index)
        if len(parts) < len(fields):
            continue
        start = ass_to_seconds(parts[start_index])
        end = ass_to_seconds(parts[end_index])
        if start is None or end is None:
            continue
        body = clean_text(parts[text_index])
        cue = Cue(start, max(end, start + 0.2), [ln for ln in body.splitlines() if ln])
        if not cue.is_empty():
            cues.append(cue)
    cues.sort(key=lambda c: c.start)
    return cues


def load_subtitle(path: str | Path) -> list[Cue]:
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    suffix = path.suffix.lower()
    if suffix in (".ass", ".ssa"):
        return parse_ass(text)
    if suffix == ".vtt":
        return parse_vtt(text)
    if "-->" in text and "Format: " in text:
        return parse_ass(text)
    return parse_srt(text)


def dedupe(cues: list[Cue], gap: float = 0.12) -> list[Cue]:
    """合并窗口化 ASR 产生的重复/相邻同句。"""
    out: list[Cue] = []
    for cue in sorted(cues, key=lambda c: (c.start, c.end)):
        if not out:
            out.append(cue)
            continue
        prev = out[-1]
        if prev.plain() == cue.plain():
            prev.end = max(prev.end, cue.end)
            continue
        if cue.start - prev.end < gap and prev.end > cue.start:
            # 时间重叠且不同文本：窗口边界切开，保留先来的一条
            prev.end = min(prev.end, cue.start)
            if prev.is_empty() or prev.end - prev.start < 0.1:
                out[-1] = cue
                continue
            continue
        out.append(cue)
    for index in range(len(out) - 1):
        if out[index].end > out[index + 1].start:
            out[index].end = max(out[index].start + 0.1, out[index + 1].start - 0.01)
    return out


_CJK = re.compile(r"[\u3000-\u9fff\uac00-\ud7af\u3040-\u30ff]")


def _pieces(text: str, limit: int) -> list[str]:
    """按标点切块，中文按字、拉丁按词，单块不超过 limit 字符。"""
    if len(text) <= limit:
        return [text]
    if _CJK.search(text):
        raw = re.split(r"(?<=[。！？；，、,.!?;])", text)
    else:
        raw = []
        current = ""
        for word in text.split(" "):
            if len(current) + len(word) + 1 > limit and current:
                raw.append(current)
                current = word
            else:
                current = f"{current} {word}".strip()
        raw.append(current)
    merged: list[str] = []
    buffer = ""
    for part in raw:
        part = part.strip()
        if not part:
            continue
        while len(part) > limit:
            merged.append(part[:limit])
            part = part[limit:]
        joined = (buffer + part) if _CJK.search(part) else f"{buffer} {part}".strip()
        if buffer and len(joined) > limit:
            merged.append(buffer)
            buffer = part
        else:
            buffer = joined
    if buffer:
        merged.append(buffer)
    return merged or [text]


def split_long_cues(cues: list[Cue], limit: int = 44, min_span: float = 1.2) -> list[Cue]:
    """whisper 常把好几句合成一条；按字符数把时长比例切开重排，读起来才像字幕。"""
    out: list[Cue] = []
    for cue in cues:
        pieces = _pieces(cue.plain(), limit)
        if len(pieces) <= 1:
            out.append(Cue(cue.start, cue.end, [cue.plain()]))
            continue
        total = sum(len(piece) for piece in pieces) or 1
        span = max(cue.end - cue.start, min_span * len(pieces))
        cursor = cue.start
        for index, piece in enumerate(pieces):
            share = span * len(piece) / total
            end = cue.end if index == len(pieces) - 1 else cursor + share
            out.append(Cue(cursor, max(end, cursor + 0.35), [piece]))
            cursor = end
    return out


@dataclass
class AssStyle:
    font: str = "PingFang SC"
    size: int = 46
    translated_size: int = 40
    color: str = "&H00FFFFFF"
    translated_color: str = "&H009BE4FF"
    outline: int = 2
    shadow: int = 1
    margin_v: int = 34
    bold: bool = True


_ASS_HEADER = """[Script Info]
; Lumina AI 字幕（自动生成，勿手改）
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,{font},{size},{color},&H00000000,&H00000000,&H80000000,{bold},0,0,0,100,100,0,0,1,{outline},{shadow},2,60,60,{margin_v},1
Style: Trans,{font},{tsize},{tcolor},&H00000000,&H00000000,&H80000000,{bold},0,0,0,100,100,0,0,1,{outline},{shadow},2,60,60,{tmargin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _escape_ass(text: str) -> str:
    text = text.replace("{", "(").replace("}", ")")
    return text.replace("|", "｜").replace("\n", "\\N")


ASS_PLAY_RES_X = 1280
ASS_SAFE_WIDTH = 1160        # 1280 - 左右各 60 的边距
ASS_MIN_FONT = 22


def _is_wide_char(ch: str) -> bool:
    code = ord(ch)
    return (0x1100 <= code <= 0x115F or 0x2E80 <= code <= 0xA4CF
            or 0xAC00 <= code <= 0xD7A3 or 0xF900 <= code <= 0xFAFF
            or 0xFE30 <= code <= 0xFE4F or 0xFF00 <= code <= 0xFF60
            or 0xFFE0 <= code <= 0xFFE6)


def ass_text_width(text: str, size: int) -> float:
    """按字号估算一行在 PlayRes 里的像素宽（中日韩字 ≈ 1 em，拉丁 ≈ 0.55 em）。"""
    total = 0.0
    for ch in text:
        if ch == "\t":
            total += size * 0.5
        elif _is_wide_char(ch):
            total += size
        elif ch.isascii():
            total += size * 0.55
        else:
            total += size * 0.8
    return total


def _wrap_text(text: str, size: int, max_width: float) -> list[str]:
    lines: list[str] = []
    current = ""
    for ch in text:
        if ch == "\n":
            lines.append(current.strip())
            current = ""
            continue
        candidate = current + ch
        if current and ass_text_width(candidate, size) > max_width:
            cut = current.rfind(" ")
            if cut > 0:
                lines.append(current[:cut].rstrip())
                current = f"{current[cut + 1:].lstrip()}{ch}"
            else:
                lines.append(current.rstrip())
                current = ch
        else:
            current = candidate
    if current.strip():
        lines.append(current.strip())
    return lines or [""]


def _norm_subtitle(text: str) -> str:
    # 只忽略空白和句读；引号/括号这类差异仍算不同，避免把 «译文» 当成原文
    return re.sub(r"[\s，。！？；：、,.!?;:…]+", "", text).lower()


_KANA_EXTRA = re.compile(r"^[\u3040-\u30ff\s、。…]+$")


def _similar_subtitle(a: str, b: str) -> bool:
    """译文只是原文加/换了个别字词时也算没翻，别在双语里显示两遍。"""
    norm_a, norm_b = _norm_subtitle(a), _norm_subtitle(b)
    if not norm_a or not norm_b:
        return False
    if norm_a == norm_b:
        return True
    if norm_a in norm_b or norm_b in norm_a:
        longer, shorter = ((norm_a, norm_b) if len(norm_a) >= len(norm_b)
                           else (norm_b, norm_a))
        extra = longer.replace(shorter, "", 1)
        if extra and len(extra) <= 6 and _KANA_EXTRA.match(extra):
            return True          # 只是多了「で」「ですね」这类日语助词
    if (len(norm_a) >= 6 and len(norm_b) >= 6
            and norm_a[0] == norm_b[0] and norm_a[-1] == norm_b[-1]
            and SequenceMatcher(None, norm_a, norm_b).ratio() >= 0.82):
        return True
    return False


def fit_ass_text(text: str, base_size: int, max_width: float = ASS_SAFE_WIDTH,
                 max_lines: int = 2, min_size: int = ASS_MIN_FONT) -> tuple[list[str], int]:
    """把一行字幕折成最多 max_lines 行；放不下就逐级缩小字号（最低 min_size）。"""
    base = max(8, int(base_size))
    floor = max(8, min(int(min_size), base))
    for size in range(base, floor - 1, -2):
        lines = _wrap_text(text, size, max_width)
        if len(lines) <= max_lines:
            return lines, size
    return _wrap_text(text, floor, max_width), floor


def write_ass(path: str | Path, cues: list[Cue], style: AssStyle | None = None,
              translated: list[str] | None = None,
              position: str = "below", keep_original: bool = True) -> Path:
    """写 ASS。

    双语用两条 Dialogue（不同 Style + 不同 MarginV），这样原文能保留自己的字号颜色，
    译文另起一行；单条 Dialogue 只能有一种样式，做不到。
    """
    style = style or AssStyle()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [_ASS_HEADER.format(
        font=style.font, size=style.size, color=style.color, tsize=style.translated_size,
        tcolor=style.translated_color, bold=-1 if style.bold else 0,
        outline=style.outline, shadow=style.shadow, margin_v=style.margin_v,
        tmargin=style.margin_v,
    )]
    def _fit(raw: str, base: int) -> tuple[str, int, int]:
        wrapped, used = fit_ass_text(raw, base)
        nonempty = [line for line in wrapped if line]
        body = "\\N".join(_escape_ass(line) for line in nonempty)
        if body and used != base:
            body = f"{{\\fs{used}}}" + body
        return body, used, max(1, len(nonempty))

    # 快语速常常让相邻两句的时间轴咬在一起；小重叠裁掉前一句的尾巴，避免两句同屏。
    # 大面积重叠视为两人同时说话，保留两条（用户接受这种同屏）。
    timings: dict[int, list[float]] = {i: [cue.start, cue.end] for i, cue in enumerate(cues)}
    order = sorted(range(len(cues)), key=lambda i: (cues[i].start, cues[i].end))
    for pos in range(1, len(order)):
        prev_i, cur_i = order[pos - 1], order[pos]
        p_start, p_end = timings[prev_i]
        c_start = timings[cur_i][0]
        overlap = p_end - c_start
        if overlap <= 0:
            continue
        p_dur = max(0.05, p_end - p_start)
        c_dur = max(0.05, timings[cur_i][1] - c_start)
        if overlap <= 0.6 or overlap < min(p_dur, c_dur) * 0.7:
            new_end = max(p_start + 0.1, c_start - 0.02)
            if new_end < p_end:
                timings[prev_i][1] = new_end

    for index, cue in enumerate(cues):
        start, end = (format_time_ass(value) for value in timings[index])
        original, original_size, original_lines = _fit(cue.plain(), style.size)
        trans = ""
        trans_size = style.translated_size
        trans_lines = 0
        if translated is not None and index < len(translated):
            raw_trans = (translated[index] or "").strip()
            if (keep_original and raw_trans
                    and _similar_subtitle(raw_trans, cue.plain())):
                raw_trans = ""       # 译文和原文相同或只差个别字词时不写第二行
            if raw_trans:
                trans, trans_size, trans_lines = _fit(raw_trans, style.translated_size)
        gap = 10
        trans_block = int(trans_size * 1.18 * max(1, trans_lines)) + gap
        original_block = int(original_size * 1.18 * max(1, original_lines)) + gap
        if trans and keep_original:
            if position == "below":
                trans_margin = style.margin_v
                main_margin = style.margin_v + trans_block
            else:
                main_margin = style.margin_v
                trans_margin = style.margin_v + original_block
            lines.append(f"Dialogue: 0,{start},{end},Trans,,0,0,{trans_margin},,{trans}")
            lines.append(f"Dialogue: 0,{start},{end},Main,,0,0,{main_margin},,{original}")
        elif trans:
            lines.append(f"Dialogue: 0,{start},{end},Trans,,0,0,{style.margin_v},,{trans}")
        elif original:
            lines.append(f"Dialogue: 0,{start},{end},Main,,0,0,{style.margin_v},,{original}")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def write_srt(path: str | Path, cues: list[Cue]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    blocks = []
    for index, cue in enumerate(cues, start=1):
        body = cue.plain()
        if not body:
            continue
        blocks.append(f"{index}\n{format_time_srt(cue.start)} --> {format_time_srt(cue.end)}\n{body}\n")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(blocks), encoding="utf-8")
    tmp.replace(path)
    return path
