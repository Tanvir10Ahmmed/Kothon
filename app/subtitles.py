"""SRT and ASS (styled, word-highlight) subtitle generation. Pure functions - no I/O."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from .transcript import Segment, Word, distribute_words

# --------------------------------------------------------------------------- time formats


def _split_ms(t: float) -> tuple[int, int, int, int]:
    total_ms = max(0, int(round(float(t) * 1000)))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return h, m, s, ms


def fmt_srt_time(t: float) -> str:
    h, m, s, ms = _split_ms(t)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def fmt_ass_time(t: float) -> str:
    total_cs = max(0, int(round(float(t) * 100)))
    h, rem = divmod(total_cs, 360_000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


# --------------------------------------------------------------------------- SRT


def to_srt(segments: Sequence[Segment]) -> str:
    blocks = []
    n = 0
    for seg in sorted(segments, key=lambda s: s.start):
        text = seg.text.strip()
        if not text or seg.end <= seg.start:
            continue
        n += 1
        blocks.append(f"{n}\n{fmt_srt_time(seg.start)} --> {fmt_srt_time(seg.end)}\n{text}\n")
    return "\n".join(blocks)


# --------------------------------------------------------------------------- ASS styling


@dataclass(frozen=True)
class FontSpec:
    family: str  # must match the family name inside the .ttf (checked by tests)


FONTS: dict[str, FontSpec] = {
    "hind": FontSpec("Hind Siliguri"),
    "mina": FontSpec("Mina"),
    "atma": FontSpec("Atma"),
}


@dataclass(frozen=True)
class Preset:
    text_color: str  # #RRGGBB
    highlight_color: str | None  # None = no word highlight
    outline_color: str
    box: bool  # opaque box behind text instead of outline
    outline_ratio: float  # outline thickness relative to font size


PRESETS: dict[str, Preset] = {
    "reels": Preset("#FFFFFF", "#FFE600", "#000000", False, 0.10),
    "classic": Preset("#FFFFFF", None, "#000000", False, 0.07),
    "box": Preset("#FFFFFF", "#FFE600", "#000000", True, 0.22),
}

SIZE_RATIO = {"small": 0.065, "medium": 0.080, "large": 0.100}  # of min(width, height)


@dataclass(frozen=True)
class CaptionStyle:
    preset: str = "reels"
    position: str = "bottom"  # bottom | middle | top
    size: str = "medium"  # small | medium | large
    font: str = "hind"
    words_per_caption: int = 3
    watermark: bool = True

    def validate(self) -> None:
        if self.preset not in PRESETS:
            raise ValueError(f"unknown preset: {self.preset}")
        if self.position not in ("bottom", "middle", "top"):
            raise ValueError(f"unknown position: {self.position}")
        if self.size not in SIZE_RATIO:
            raise ValueError(f"unknown size: {self.size}")
        if self.font not in FONTS:
            raise ValueError(f"unknown font: {self.font}")
        if not 1 <= int(self.words_per_caption) <= 6:
            raise ValueError("words_per_caption must be 1..6")


def ass_color(hex_rgb: str, alpha: int = 0) -> str:
    """'#RRGGBB' -> '&HAABBGGRR' (ASS uses BGR; alpha 0=opaque, 255=transparent)."""
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ass_override_color(hex_rgb: str) -> str:
    h = hex_rgb.lstrip("#")
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def escape_ass(text: str) -> str:
    """Neutralise ASS special characters. Keeps U+200C/U+200D (needed by Bangla shaping)."""
    text = _CONTROL_RE.sub("", text)
    text = text.replace("\\", "").replace("{", "(").replace("}", ")")
    text = re.sub(r"[\r\n\t]+", " ", text)
    return text.strip()


# --------------------------------------------------------------------------- chunking


def chunk_words(words: Sequence[Word], max_words: int, max_chars: int = 34) -> list[list[Word]]:
    """Group words into on-screen caption chunks (<= max_words and roughly <= max_chars)."""
    chunks: list[list[Word]] = []
    cur: list[Word] = []
    cur_chars = 0
    for w in words:
        add = len(w.text) + (1 if cur else 0)
        if cur and (len(cur) >= max_words or cur_chars + add > max_chars):
            chunks.append(cur)
            cur, cur_chars = [], 0
            add = len(w.text)
        cur.append(w)
        cur_chars += add
    if cur:
        chunks.append(cur)
    return chunks


@dataclass
class _Event:
    start: float
    end: float
    text: str
    style: str = "Caption"


HOLD_AFTER_WORD = 0.30  # keep last word visible a bit after it was spoken
CONTINUOUS_GAP = 0.35  # if the next word starts within this gap, bridge it (no flicker)


def _segment_words(seg: Segment) -> list[Word]:
    words = [w for w in seg.words if w.text.strip()]
    if " ".join(w.text for w in words).split() != seg.text.split():
        words = distribute_words(seg.text, seg.start, seg.end)  # defensive: text/words out of sync
    return words


def _caption_events(segments: Sequence[Segment], style: CaptionStyle) -> list[_Event]:
    preset = PRESETS[style.preset]
    events: list[_Event] = []
    for seg in sorted(segments, key=lambda s: s.start):
        words = _segment_words(seg)
        if not words:
            continue
        chunks = chunk_words(words, int(style.words_per_caption))
        for ci, chunk in enumerate(chunks):
            next_chunk_start = chunks[ci + 1][0].start if ci + 1 < len(chunks) else None
            chunk_end = chunk[-1].end
            if next_chunk_start is not None and next_chunk_start - chunk_end < CONTINUOUS_GAP:
                chunk_end = max(chunk_end, next_chunk_start)
            else:
                chunk_end += HOLD_AFTER_WORD
            if preset.highlight_color is None:
                text = " ".join(escape_ass(w.text) for w in chunk)
                events.append(_Event(chunk[0].start, chunk_end, text))
                continue
            hl = _ass_override_color(preset.highlight_color)
            base = _ass_override_color(preset.text_color)
            for wi, w in enumerate(chunk):
                parts = []
                for wj, other in enumerate(chunk):
                    t = escape_ass(other.text)
                    parts.append(f"{{\\1c{hl}}}{t}{{\\1c{base}}}" if wi == wj else t)
                start = w.start
                end = chunk[wi + 1].start if wi + 1 < len(chunk) else chunk_end
                events.append(_Event(start, max(end, start), " ".join(parts)))
    # resolve overlaps: a later event always wins, tiny events are dropped
    events.sort(key=lambda e: (e.start, e.end))
    cleaned: list[_Event] = []
    for i, ev in enumerate(events):
        if i + 1 < len(events) and ev.end > events[i + 1].start:
            ev.end = events[i + 1].start
        if ev.end - ev.start >= 0.03 and ev.text.strip():
            cleaned.append(ev)
    return cleaned


def build_ass(
    segments: Sequence[Segment],
    style: CaptionStyle,
    width: int,
    height: int,
    watermark_text: str | None = None,
    duration: float | None = None,
) -> str:
    """Return a complete .ass document for the given (display) video size."""
    style.validate()
    preset = PRESETS[style.preset]
    font = FONTS[style.font]
    side = min(width, height)
    fs = max(12, int(round(side * SIZE_RATIO[style.size])))
    outline = max(1, int(round(fs * preset.outline_ratio)))
    if style.position == "bottom":
        alignment, margin_v = 2, int(round(height * 0.17))  # above Reels/TikTok UI
    elif style.position == "top":
        alignment, margin_v = 8, int(round(height * 0.12))
    else:
        alignment, margin_v = 5, 0
    margin_lr = int(round(width * 0.06))

    primary = ass_color(preset.text_color)
    if preset.box:
        border_style = 3
        outline_col = ass_color("#000000", alpha=0x18)  # box colour (near-opaque: translucent boxes show seams between words)
        back_col = ass_color("#000000", alpha=0x18)
    else:
        border_style = 1
        outline_col = ass_color(preset.outline_color)
        back_col = ass_color("#000000", alpha=0x80)

    wm_fs = max(10, int(round(side * 0.032)))
    wm_margin_v = int(round(height * 0.04))

    lines = [
        "[Script Info]",
        "; Generated by Kothon",
        "ScriptType: v4.00+",
        f"PlayResX: {int(width)}",
        f"PlayResY: {int(height)}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Caption,{font.family},{fs},{primary},{primary},{outline_col},{back_col},"
        f"-1,0,0,0,100,100,0,0,{border_style},{outline},0,{alignment},{margin_lr},{margin_lr},{margin_v},1",
        f"Style: Watermark,{font.family},{wm_fs},{ass_color('#FFFFFF', 0x40)},{ass_color('#FFFFFF', 0x40)},"
        f"{ass_color('#000000', 0x60)},{ass_color('#000000', 0x80)},"
        f"-1,0,0,0,100,100,0,0,1,{max(1, wm_fs // 12)},0,8,{margin_lr},{margin_lr},{wm_margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for ev in _caption_events(segments, style):
        lines.append(f"Dialogue: 0,{fmt_ass_time(ev.start)},{fmt_ass_time(ev.end)},{ev.style},,0,0,0,,{ev.text}")

    if style.watermark and watermark_text:
        wm_end = duration if duration else max([s.end for s in segments] + [60.0]) + 5
        lines.append(
            f"Dialogue: 1,{fmt_ass_time(0)},{fmt_ass_time(wm_end + 1)},Watermark,,0,0,0,,{escape_ass(watermark_text)}"
        )
    return "\n".join(lines) + "\n"
