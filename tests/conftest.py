from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402
from app.transcript import Segment, Word  # noqa: E402


class FakeTranscriber:
    """Deterministic stand-in for Whisper so the whole pipeline can be tested without a model download."""

    def __init__(self, fail: bool = False, empty: bool = False):
        self.fail = fail
        self.empty = empty
        self.calls = 0

    def transcribe(self, audio_path, model_key, duration, on_stage, on_progress):
        self.calls += 1
        assert Path(audio_path).exists()
        on_stage("loading_model", "fake load")
        on_stage("transcribing", "fake run")
        on_progress(50)
        if self.fail:
            from app.transcribe import TranscribeError

            raise TranscribeError("fake failure")
        if self.empty:
            return []
        data = [
            (0.2, 1.4, "আমি বাংলায় কথা বলছি"),
            (1.6, 2.8, "এটা একটা ক্যাপশন টেস্ট"),
            (3.0, 4.6, "বিশ্ব বাংলা ক্ষমতা দৃষ্টি র‍্যাব"),  # conjuncts + ZWJ
        ]
        segs = []
        for i, (s, e, t) in enumerate(data, 1):
            toks = t.split()
            step = (e - s) / len(toks)
            words = [Word(tok, s + k * step, s + (k + 1) * step) for k, tok in enumerate(toks)]
            segs.append(Segment(i, s, e, t, words))
        return segs


@pytest.fixture()
def settings(tmp_path) -> Settings:
    base = Path(__file__).resolve().parent.parent
    return Settings(
        data_dir=tmp_path / "data",
        fonts_dir=base / "fonts",
        static_dir=base / "static",
        max_seconds=10,
        max_upload_mb=20,
        cpu_threads=2,
    )


def make_video(path: Path, seconds: float = 5, size: str = "320x568", audio: bool = True, extra_in: list[str] | None = None,
                extra_out: list[str] | None = None) -> Path:
    """Synthesize a small test video with ffmpeg."""
    cmd = ["ffmpeg", "-y", "-v", "error"]
    cmd += ["-f", "lavfi", "-i", f"testsrc2=size={size}:rate=25:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd += extra_out or []
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd += [str(path)]
    subprocess.run(cmd, check=True)
    return path


def wait_for(fn, timeout: float = 60, interval: float = 0.1):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(interval)
    raise AssertionError("timeout waiting for condition")
