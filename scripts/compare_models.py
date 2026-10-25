#!/usr/bin/env python3
"""A/B the models on YOUR real videos before you promise accuracy to anyone.

    python scripts/compare_models.py clip1.mp4 clip2.mov [--models bn-medium large-v3]

Prints the Bangla text each model produced + speed. Compare with what was actually said.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import video  # noqa: E402
from app.config import MODEL_PRESETS, Settings  # noqa: E402
from app.transcribe import WhisperTranscriber  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--models", nargs="+", default=list(MODEL_PRESETS))
    args = ap.parse_args()
    settings = Settings.from_env()
    tr = WhisperTranscriber(settings)
    for f in args.files:
        src = Path(f).expanduser()
        info = video.probe(src, settings.ffprobe)
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "a.wav"
            video.extract_audio(src, wav, settings.ffmpeg)
            print(f"\n=== {src.name}  ({info.duration:.1f}s) ===")
            for key in args.models:
                t0 = time.time()
                segs = tr.transcribe(wav, key, info.duration, lambda s, m: print(f"   [{key}] {m}") if s == "loading_model" else None, lambda p: None)
                dt = time.time() - t0
                print(f"\n--- {key}: {dt:.1f}s  ({dt / info.duration:.2f}x realtime) ---")
                for s in segs:
                    print(f"  [{s.start:5.1f}-{s.end:5.1f}] {s.text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
