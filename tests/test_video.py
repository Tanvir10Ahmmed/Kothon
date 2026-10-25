from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app import video
from app.subtitles import CaptionStyle, build_ass
from app.transcript import Segment, distribute_words

from conftest import make_video

FONTS = Path(__file__).resolve().parent.parent / "fonts"
TEXT = "বিশ্ব বাংলা ক্ষমতা দৃষ্টি"


def _segs():
    return [Segment(1, 0.2, 1.6, TEXT, distribute_words(TEXT, 0.2, 1.6))]


def _frame_bytes(path: Path, t: float, w: int, h: int) -> bytes:
    return subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{w}x{h}", "-"], capture_output=True, check=True,
    ).stdout


def _diff_ratio(a: bytes, b: bytes) -> float:
    assert len(a) == len(b) and len(a) > 0
    return sum(1 for x, y in zip(a[::3], b[::3]) if abs(x - y) > 40) / (len(a) / 3)


def test_probe_ok_and_errors(tmp_path):
    p = make_video(tmp_path / "a.mp4", 2, "320x240")
    info = video.probe(p)
    assert (info.width, info.height, info.has_audio) == (320, 240, True)
    assert info.duration == pytest.approx(2.0, abs=0.2)
    noaudio = make_video(tmp_path / "b.mp4", 2, "320x240", audio=False)
    assert video.probe(noaudio).has_audio is False
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"this is not a video" * 100)
    with pytest.raises(video.VideoError):
        video.probe(junk)
    audio_only = tmp_path / "a.m4a"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=duration=1", str(audio_only)], check=True)
    with pytest.raises(video.VideoError):
        video.probe(audio_only)


def test_rotation_swaps_display_size(tmp_path):
    # phone videos: stored landscape, flagged as rotated 90 deg
    src = make_video(tmp_path / "land.mp4", 2, "320x180")
    rot = tmp_path / "rot.mp4"
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-display_rotation", "90", "-i", str(src), "-c", "copy", str(rot)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("this ffmpeg cannot write display rotation")
    info = video.probe(rot)
    assert info.rotation in (90, 270)
    assert (info.width, info.height) == (180, 320)


def test_output_size_even_and_bounded():
    assert video.output_size(1080, 1920) == (1080, 1920)
    assert video.output_size(3840, 2160) == (1920, 1080)
    w, h = video.output_size(1001, 1777)
    assert w % 2 == 0 and h % 2 == 0
    assert video.output_size(2160, 3840) == (1080, 1920)
    assert min(video.output_size(3, 3)) >= 2


def test_extract_audio(tmp_path):
    p = make_video(tmp_path / "a.mp4", 2, "320x240")
    out = tmp_path / "a.wav"
    video.extract_audio(p, out)
    assert out.stat().st_size > 50_000
    silent = make_video(tmp_path / "s.mp4", 2, "320x240", audio=False)
    with pytest.raises(video.VideoError):
        video.extract_audio(silent, tmp_path / "s.wav")


@pytest.mark.parametrize("size", ["320x568", "480x270", "301x401"])
def test_render_burns_captions_and_keeps_av(tmp_path, size):
    src = make_video(tmp_path / "in.mp4", 3, size)
    info = video.probe(src)
    w, h = video.output_size(info.width, info.height)
    ass = build_ass(_segs(), CaptionStyle(preset="classic", watermark=False), w, h)
    out = tmp_path / "out.mp4"
    progress = []
    video.render_video(src, out, ass, info, FONTS, tmp_path / "work dir: with 'odd' chars", on_progress=progress.append)
    oi = video.probe(out)
    assert (oi.width, oi.height) == (w, h)
    assert oi.has_audio and oi.duration == pytest.approx(3.0, abs=0.3)
    assert progress and progress[-1] == 1.0 and all(0 <= p <= 1 for p in progress)
    # captions are visible during the cue and absent after it
    with_caption = _frame_bytes(out, 1.0, 160, 160)
    plain = tmp_path / "plain.mp4"
    video.render_video(src, plain, build_ass([], CaptionStyle(watermark=False), w, h), info, FONTS, tmp_path / "w2")
    assert _diff_ratio(with_caption, _frame_bytes(plain, 1.0, 160, 160)) > 0.002
    assert _diff_ratio(_frame_bytes(out, 2.6, 160, 160), _frame_bytes(plain, 2.6, 160, 160)) < 0.001


def test_render_rotated_phone_video_has_display_orientation(tmp_path):
    src = make_video(tmp_path / "land.mp4", 2, "320x180")
    rot = tmp_path / "rot.mp4"
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-display_rotation", "90", "-i", str(src), "-c", "copy", str(rot)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("this ffmpeg cannot write display rotation")
    info = video.probe(rot)
    w, h = video.output_size(info.width, info.height)
    out = tmp_path / "out.mp4"
    video.render_video(rot, out, build_ass(_segs(), CaptionStyle(), w, h), info, FONTS, tmp_path / "w")
    oi = video.probe(out)
    assert (oi.width, oi.height) == (w, h) and h > w


def test_render_frame_png(tmp_path):
    src = make_video(tmp_path / "in.mp4", 3, "320x568")
    info = video.probe(src)
    w, h = video.output_size(info.width, info.height)
    png = tmp_path / "f.png"
    video.render_frame(src, png, build_ass(_segs(), CaptionStyle(), w, h, "wm", 3), info, 1.0, FONTS, tmp_path / "pw")
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    # time past the end must not crash
    video.render_frame(src, png, build_ass(_segs(), CaptionStyle(), w, h), info, 999, FONTS, tmp_path / "pw")


def test_render_failure_is_reported(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"x" * 5000)
    info = video.VideoInfo(3, 320, 240, True, 0)
    with pytest.raises(video.ToolError):
        video.render_video(bad, tmp_path / "o.mp4", build_ass([], CaptionStyle(), 320, 240), info, FONTS, tmp_path / "w")
