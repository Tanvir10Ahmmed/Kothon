"""Transcript data model + pure helpers (word timing repair, re-splitting, reconcile after edit)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

MIN_WORD_DURATION = 0.05  # seconds


@dataclass
class Word:
    text: str
    start: float
    end: float

    def to_dict(self) -> dict:
        return {"text": self.text, "start": round(self.start, 3), "end": round(self.end, 3)}

    @classmethod
    def from_dict(cls, d: dict) -> "Word":
        return cls(str(d["text"]), float(d["start"]), float(d["end"]))


@dataclass
class Segment:
    id: int
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "text": self.text,
            "words": [w.to_dict() for w in self.words],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Segment":
        return cls(
            id=int(d["id"]),
            start=float(d["start"]),
            end=float(d["end"]),
            text=str(d["text"]),
            words=[Word.from_dict(w) for w in d.get("words", [])],
        )


def split_words(text: str) -> list[str]:
    """Whitespace split. (Zero-width joiners used in Bangla are NOT whitespace, so they survive.)"""
    return text.split()


def distribute_words(text: str, start: float, end: float) -> list[Word]:
    """Spread words across [start, end] proportionally to their length. Always valid output."""
    tokens = split_words(text)
    if not tokens:
        return []
    start = float(start)
    end = max(float(end), start + MIN_WORD_DURATION * len(tokens))
    total = sum(len(t) + 1 for t in tokens)  # +1 so very short words still get time
    span = end - start
    words: list[Word] = []
    cursor = start
    for i, tok in enumerate(tokens):
        share = span * (len(tok) + 1) / total
        w_end = end if i == len(tokens) - 1 else cursor + share
        words.append(Word(tok, cursor, w_end))
        cursor = w_end
    return words


def _words_valid(words: list[Word], seg_start: float, seg_end: float, eps: float = 1e-6) -> bool:
    prev_end = seg_start - eps
    for w in words:
        if w.start < prev_end - eps or w.end < w.start + MIN_WORD_DURATION - eps:
            return False
        prev_end = w.end
    return words[-1].end <= seg_end + 0.25 if words else True


def sanitize_words(words: list[Word], seg_start: float, seg_end: float) -> list[Word]:
    """Make model-produced word timings safe: clamp, monotonic, min duration.

    If we cannot repair them (garbage input) the words are re-distributed evenly.
    """
    words = [Word(w.text, w.start, w.end) for w in words if w.text.strip()]
    if not words:
        return []
    text = " ".join(w.text for w in words)
    seg_end = max(seg_end, seg_start + MIN_WORD_DURATION * len(words))
    cursor = seg_start
    for w in words:
        w.start = min(max(w.start, cursor), seg_end)
        w.end = max(w.end, w.start + MIN_WORD_DURATION)
        cursor = w.end
    if not _words_valid(words, seg_start, seg_end):
        return distribute_words(text, seg_start, seg_end)
    if words[-1].end > seg_end + 0.25:  # pushed too far by min-duration cascade
        return distribute_words(text, seg_start, seg_end)
    return words


def retime_words(words: list[Word], old_start: float, old_end: float, new_start: float, new_end: float) -> list[Word]:
    old_dur = old_end - old_start
    if old_dur <= 1e-6 or (abs(old_start - new_start) < 1e-9 and abs(old_end - new_end) < 1e-9):
        return [Word(w.text, w.start, w.end) for w in words]
    scale = (new_end - new_start) / old_dur
    return [Word(w.text, new_start + (w.start - old_start) * scale, new_start + (w.end - old_start) * scale) for w in words]


def reconcile_segment(orig: Segment | None, seg_id: int, text: str, start: float, end: float) -> Segment:
    """Build a Segment from user-edited text/times, keeping the original word timings when possible."""
    text = " ".join(split_words(text))
    new_tokens = split_words(text)
    words: list[Word]
    if orig is not None and orig.words and len(orig.words) == len(new_tokens):
        base = retime_words(orig.words, orig.start, orig.end, start, end)
        words = [Word(tok, w.start, w.end) for tok, w in zip(new_tokens, base)]
        words = sanitize_words(words, start, end)
    else:
        words = distribute_words(text, start, end)
    return Segment(id=seg_id, start=start, end=end, text=text, words=words)


_SENTENCE_END = ("।", ".", "?", "!", "…")


def resplit_segments(
    segments: Iterable[Segment],
    max_words: int = 8,
    max_chars: int = 46,
    max_duration: float = 4.5,
    gap_split: float = 0.7,
) -> list[Segment]:
    """Cut long Whisper segments into subtitle-sized pieces at word boundaries, renumber ids from 1."""
    out: list[Segment] = []

    def flush(buf: list[Word]) -> None:
        if not buf:
            return
        text = " ".join(w.text for w in buf)
        out.append(Segment(id=0, start=buf[0].start, end=buf[-1].end, text=text, words=list(buf)))

    for seg in segments:
        words = seg.words or distribute_words(seg.text, seg.start, seg.end)
        buf: list[Word] = []
        for w in words:
            if buf:
                chars = sum(len(x.text) + 1 for x in buf) + len(w.text)
                too_long = (
                    len(buf) >= max_words
                    or chars > max_chars
                    or (w.end - buf[0].start) > max_duration
                    or (w.start - buf[-1].end) > gap_split
                )
                if too_long:
                    flush(buf)
                    buf = []
            buf.append(w)
            if w.text.endswith(_SENTENCE_END) and len(buf) >= 3:
                flush(buf)
                buf = []
        flush(buf)

    # avoid overlaps between neighbours and give stable ids
    out.sort(key=lambda s: (s.start, s.end))
    for i, s in enumerate(out):
        s.id = i + 1
        if i + 1 < len(out) and s.end > out[i + 1].start:
            s.end = max(out[i + 1].start, s.start + MIN_WORD_DURATION)
            s.words = sanitize_words(s.words, s.start, s.end)
    return out


def apply_edits(orig: list[Segment], incoming: list[dict], duration: float) -> list[Segment]:
    """Validate + normalise the editor's segment list. Raises ValueError with a user-facing message.

    - empty text  -> segment is deleted
    - unknown/duplicate ids -> treated as new segments
    - times clamped to the video, tiny overlaps trimmed, real problems rejected
    """
    by_id = {s.id: s for s in orig}
    next_id = max([0, *by_id.keys(), *[int(d.get("id", 0)) for d in incoming]]) + 1
    used: set[int] = set()
    items: list[Segment] = []
    for d in incoming:
        text = " ".join(str(d.get("text", "")).split())
        if not text:
            continue
        start = max(0.0, float(d["start"]))
        end = min(float(d["end"]), float(duration))
        if start >= duration - 0.05:
            raise ValueError(f"Caption ({start:.1f}s) video er shesh er baire.")
        if end - start < 0.1:
            raise ValueError(f"Caption ({start:.1f}s) er end time start er por hote hobe (kompokkhe 0.1s).")
        sid = int(d.get("id", -1))
        orig_seg = by_id.get(sid) if sid not in used else None
        if sid in used or sid < 0:
            sid = next_id
            next_id += 1
        used.add(sid)
        items.append(reconcile_segment(orig_seg, sid, text, start, end))
    items.sort(key=lambda s: (s.start, s.end))
    for i in range(len(items) - 1):
        cur, nxt = items[i], items[i + 1]
        if cur.end > nxt.start:
            if nxt.start - cur.start < 0.05:
                raise ValueError(f"Duita caption ek-i somoy ({cur.start:.1f}s) e shuru hocche. Time thik koro.")
            cur.end = nxt.start
            cur.words = sanitize_words(cur.words, cur.start, cur.end)
    return items
