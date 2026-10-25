from __future__ import annotations

import re

import pytest

from app.subtitles import (
    FONTS, CaptionStyle, ass_color, build_ass, chunk_words, escape_ass, fmt_ass_time, fmt_srt_time, to_srt,
)
from app.transcript import (
    Segment, Word, apply_edits, distribute_words, reconcile_segment, resplit_segments, sanitize_words,
)
from app.transcribe import build_segments


def seg(id, s, e, text):
    return Segment(id, s, e, text, distribute_words(text, s, e))


def test_time_formats():
    assert fmt_srt_time(0) == "00:00:00,000"
    assert fmt_srt_time(3661.5) == "01:01:01,500"
    assert fmt_srt_time(1.9996) == "00:00:02,000"  # rounding carries correctly
    assert fmt_ass_time(0) == "0:00:00.00"
    assert fmt_ass_time(59.999) == "0:01:00.00"
    assert fmt_ass_time(3661.5) == "1:01:01.50"
    assert fmt_srt_time(-5) == "00:00:00,000"


def test_srt_output():
    out = to_srt([seg(2, 2, 3, "দুই"), seg(1, 0, 1.5, "এক"), seg(3, 3, 3, "skip"), seg(4, 4, 5, "  ")])
    assert out == "1\n00:00:00,000 --> 00:00:01,500\nএক\n\n2\n00:00:02,000 --> 00:00:03,000\nদুই\n"


def test_ass_color():
    assert ass_color("#FFE600") == "&H0000E6FF"
    assert ass_color("#102030", 0x80) == "&H80302010"


def test_escape_keeps_bangla_joiners_and_removes_ass_syntax():
    zwj = "র\u200d্য"  # র + ZWJ + ্য
    assert "\u200d" in escape_ass(zwj)
    assert escape_ass("a{\\b1}b\\Nc\nd") == "a(b1)bNc d"
    assert escape_ass("x\x00y\x07z") == "xyz"


def test_distribute_words_valid():
    ws = distribute_words("এক দুই তিন চার", 1.0, 3.0)
    assert len(ws) == 4
    assert ws[0].start == pytest.approx(1.0) and ws[-1].end == pytest.approx(3.0)
    for a, b in zip(ws, ws[1:]):
        assert a.end == pytest.approx(b.start)
        assert a.end > a.start
    assert distribute_words("   ", 0, 1) == []
    z = distribute_words("a b c", 5, 5)  # zero length segment must still yield valid words
    assert all(w.end > w.start for w in z)


def test_sanitize_words_repairs_garbage():
    bad = [Word("a", 2.0, 1.0), Word("b", 0.5, 0.5), Word("c", 9.0, 12.0)]
    out = sanitize_words(bad, 1.0, 3.0)
    assert [w.text for w in out] == ["a", "b", "c"]
    prev = 0
    for w in out:
        assert w.start >= prev - 1e-9 and w.end > w.start
        prev = w.end
    assert out[-1].end <= 3.0 + 0.25 + 1e-9


def test_reconcile_keeps_timing_when_word_count_same():
    orig = Segment(1, 1.0, 3.0, "এক দুই", [Word("এক", 1.0, 1.5), Word("দুই", 1.5, 3.0)])
    new = reconcile_segment(orig, 1, "এক তিন", 1.0, 3.0)
    assert [(w.text, w.start, w.end) for w in new.words] == [("এক", 1.0, 1.5), ("তিন", 1.5, 3.0)]
    moved = reconcile_segment(orig, 1, "এক দুই", 2.0, 4.0)  # retime
    assert moved.words[0].start == pytest.approx(2.0) and moved.words[1].end == pytest.approx(4.0)
    longer = reconcile_segment(orig, 1, "এক দুই তিন", 1.0, 3.0)  # count differs -> redistribute
    assert len(longer.words) == 3 and longer.words[-1].end == pytest.approx(3.0)


def test_resplit_long_segment():
    text = " ".join(f"শব্দ{i}" for i in range(20))
    long = Segment(1, 0, 10, text, distribute_words(text, 0, 10))
    parts = resplit_segments([long])
    assert len(parts) >= 3
    assert [p.id for p in parts] == list(range(1, len(parts) + 1))
    assert " ".join(p.text for p in parts) == text
    for a, b in zip(parts, parts[1:]):
        assert a.end <= b.start + 1e-9


def test_build_segments_handles_mismatched_and_empty_words():
    raw = [
        (0, 2, " আমি ভালো আছি ", [(" আমি", 0.0, 0.5), (" ভালো", 0.5, 1.0), (" আছি", 1.0, 2.0)]),
        (2, 3, "ভুল শব্দ", [(" অন্য", 2.0, 2.5)]),  # words don't match text -> ignored, redistributed
        (3, 4, "   ", None),  # empty -> dropped
        (4, 5, "শুধু টেক্সট", None),
    ]
    segs = build_segments(raw)
    assert [s.text for s in segs] == ["আমি ভালো আছি", "ভুল শব্দ", "শুধু টেক্সট"]
    assert all(s.words and " ".join(w.text for w in s.words) == s.text for s in segs)


def test_repetition_loop_dropped():
    raw = [(i, i + 0.9, "একই কথা", None) for i in range(6)]
    assert len(build_segments(raw)) == 2


def test_chunk_words():
    words = distribute_words("এক দুই তিন চার পাঁচ ছয় সাত", 0, 7)
    chunks = chunk_words(words, 3)
    assert [len(c) for c in chunks] == [3, 3, 1]
    long = distribute_words("অতিদীর্ঘশব্দএকঅতিদীর্ঘশব্দদুইঅতিদীর্ঘ আরেকটি", 0, 2)
    assert len(chunk_words(long, 6, max_chars=20)) == 2


DIALOGUE = re.compile(r"^Dialogue: (\d+),(\d+:\d\d:\d\d\.\d\d),(\d+:\d\d:\d\d\.\d\d),(\w+),,0,0,0,,(.*)$")


def _events(ass: str):
    out = []
    for line in ass.splitlines():
        if line.startswith("Dialogue:"):
            m = DIALOGUE.match(line)
            assert m, line
            def secs(t):
                h, m_, s = t.split(":")
                return int(h) * 3600 + int(m_) * 60 + float(s)
            out.append((secs(m.group(2)), secs(m.group(3)), m.group(4), m.group(5)))
    return out


@pytest.mark.parametrize("preset", ["reels", "classic", "box"])
@pytest.mark.parametrize("position", ["bottom", "middle", "top"])
def test_build_ass_structure(preset, position):
    segs = [seg(1, 0.5, 2.5, "আমি বাংলায় কথা বলছি আজ"), seg(2, 3, 4, "দ্বিতীয় লাইন")]
    ass = build_ass(segs, CaptionStyle(preset=preset, position=position), 1080, 1920, "কথন দিয়ে তৈরি", 5.0)
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    styles = [l for l in ass.splitlines() if l.startswith("Style:")]
    assert len(styles) == 2 and all(len(s.split(",")) == 23 for s in styles)  # matches Format: column count
    ev = _events(ass)
    caps = [e for e in ev if e[2] == "Caption"]
    assert caps and any(e[2] == "Watermark" for e in ev)
    # no overlaps between caption events, all positive duration
    for a, b in zip(caps, caps[1:]):
        assert a[1] <= b[0] + 1e-6
    assert all(e[1] > e[0] for e in ev)


def test_highlight_has_one_highlighted_word_per_event_and_classic_none():
    segs = [seg(1, 0, 3, "এক দুই তিন")]
    ev = [e for e in _events(build_ass(segs, CaptionStyle(preset="reels"), 720, 1280)) if e[2] == "Caption"]
    assert len(ev) == 3
    for e in ev:
        assert e[3].count("\\1c&H00E6FF&") == 1
    ev2 = [e for e in _events(build_ass(segs, CaptionStyle(preset="classic"), 720, 1280)) if e[2] == "Caption"]
    assert len(ev2) == 1 and "\\1c" not in ev2[0][3] and ev2[0][3] == "এক দুই তিন"


def test_no_watermark_when_disabled_or_text_empty():
    segs = [seg(1, 0, 1, "এক")]
    assert "Watermark," not in "\n".join(l for l in build_ass(segs, CaptionStyle(watermark=False), 720, 1280, "x").splitlines() if l.startswith("Dialogue"))
    assert not any(e[2] == "Watermark" for e in _events(build_ass(segs, CaptionStyle(), 720, 1280, "")))


def test_invalid_style_rejected():
    with pytest.raises(ValueError):
        build_ass([], CaptionStyle(preset="nope"), 100, 100)
    with pytest.raises(ValueError):
        build_ass([], CaptionStyle(words_per_caption=0), 100, 100)


def test_font_families_match_bundled_files():
    import subprocess
    from pathlib import Path

    fonts = Path(__file__).resolve().parent.parent / "fonts"
    found = set()
    for f in fonts.glob("*.ttf"):
        out = subprocess.run(["fc-scan", "--format", "%{family}", str(f)], capture_output=True, text=True).stdout
        found.add(out.split(",")[0].strip())
    assert {f.family for f in FONTS.values()} <= found


def test_apply_edits_rules():
    orig = [seg(1, 0, 2, "এক দুই"), seg(2, 2, 4, "তিন চার")]
    out = apply_edits(orig, [{"id": 2, "start": 2, "end": 4, "text": "তিন পাঁচ"}, {"id": 1, "start": 0, "end": 2, "text": "এক দুই"},
                             {"id": 9, "start": 5, "end": 6, "text": ""}], 10)
    assert [s.text for s in out] == ["এক দুই", "তিন পাঁচ"]  # sorted, empty dropped
    assert out[1].words[0].start == pytest.approx(2.0)
    # overlap trimmed
    out = apply_edits(orig, [{"id": 1, "start": 0, "end": 3, "text": "এক দুই"}, {"id": 2, "start": 2, "end": 4, "text": "তিন"}], 10)
    assert out[0].end == pytest.approx(2.0)
    # clamp to duration
    out = apply_edits(orig, [{"id": 1, "start": 8, "end": 15, "text": "এক"}], 10)
    assert out[0].end == pytest.approx(10.0)
    # duplicates / unknown ids get fresh ids
    out = apply_edits(orig, [{"id": 1, "start": 0, "end": 1, "text": "ক"}, {"id": 1, "start": 1, "end": 2, "text": "খ"}, {"id": -1, "start": 2, "end": 3, "text": "গ"}], 10)
    assert len({s.id for s in out}) == 3
    for bad in ([{"id": 1, "start": 3, "end": 3.05, "text": "ক"}], [{"id": 1, "start": 10, "end": 12, "text": "ক"}],
                [{"id": 1, "start": 1, "end": 2, "text": "ক"}, {"id": 2, "start": 1.01, "end": 3, "text": "খ"}]):
        with pytest.raises(ValueError):
            apply_edits(orig, bad, 10)
