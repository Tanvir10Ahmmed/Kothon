#!/usr/bin/env python3
"""Environment self-check. Run after setup:  python scripts/doctor.py

Verifies Python, ffmpeg (+ the 'ass' subtitle filter and libx264), bundled Bangla fonts, faster-whisper,
and renders a real Bangla sample frame to data/doctor-sample.png so YOU can eyeball the conjuncts.
"""
from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OK, WARN, FAIL = "\033[32m✓\033[0m", "\033[33m!\033[0m", "\033[31m✗\033[0m"
failures = 0


def report(kind: str, msg: str) -> None:
    global failures
    if kind == FAIL:
        failures += 1
    print(f" {kind} {msg}")


def run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout


def main() -> int:
    print(f"Kothon doctor  ({platform.system()} {platform.machine()}, Python {platform.python_version()})")

    v = sys.version_info
    if (3, 10) <= (v.major, v.minor) <= (3, 13):
        report(OK, f"Python {v.major}.{v.minor}")
        if (v.major, v.minor) not in ((3, 11), (3, 12)):
            report(WARN, "Tested on Python 3.11/3.12. Onno version e kaj korte pare, kintu 3.12 recommended.")
    else:
        report(FAIL, f"Python {v.major}.{v.minor} supported na. `brew install python@3.12` kore setup.sh abar chalao.")

    from app.config import Settings

    settings = Settings.from_env()
    for tool in (settings.ffmpeg, settings.ffprobe):
        if shutil.which(tool):
            report(OK, f"{tool} -> {shutil.which(tool)}")
        else:
            report(FAIL, f"{tool} paoa jayni. `brew install ffmpeg` chalao.")
    ffmpeg_ok = bool(shutil.which(settings.ffmpeg))

    if ffmpeg_ok:
        filters = run([settings.ffmpeg, "-hide_banner", "-filters"])
        if " ass " in filters:
            report(OK, "ffmpeg 'ass' subtitle filter (libass) ache")
        else:
            report(FAIL, "ffmpeg e libass nei -> caption burn hobe na. `brew reinstall ffmpeg` (ba `brew install homebrew-ffmpeg/ffmpeg/ffmpeg`).")
        enc = run([settings.ffmpeg, "-hide_banner", "-encoders"])
        report(OK if "libx264" in enc else FAIL, "H.264 encoder (libx264)" + ("" if "libx264" in enc else " nei"))

    fonts = sorted(settings.fonts_dir.glob("*.ttf"))
    report(OK if len(fonts) >= 3 else FAIL, f"Bangla fonts bundled: {', '.join(f.stem for f in fonts) or 'none'}")

    try:
        import faster_whisper
        import ctranslate2

        report(OK, f"faster-whisper {faster_whisper.__version__}, ctranslate2 {ctranslate2.__version__}")
        from app.transcribe import pick_compute_type

        report(OK, f"CPU compute type: {pick_compute_type()} (supported: {', '.join(sorted(ctranslate2.get_supported_compute_types('cpu')))})")
    except Exception as e:  # noqa: BLE001
        report(FAIL, f"faster-whisper import hoyni: {e}")

    # Voice-activity detection (onnxruntime) is used on every video: prove it runs on THIS machine.
    try:
        import numpy as np
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        get_speech_timestamps(np.zeros(16000 * 2, dtype=np.float32), VadOptions(min_silence_duration_ms=500))
        report(OK, "Voice detection (onnxruntime / VAD) kaj korche")
    except Exception as e:  # noqa: BLE001
        report(FAIL, f"Voice detection (onnxruntime) cholche na: {e}. Try: .venv/bin/pip install -U onnxruntime")

    for mod in ("fastapi", "uvicorn", "multipart"):
        try:
            __import__(mod)
        except ImportError:
            report(FAIL, f"python package '{mod}' nei. setup.sh abar chalao.")
    report(OK, f"CPU threads available: {os.cpu_count()} (Kothon uses {settings.cpu_threads}; KOTHON_THREADS diye change)")

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    free_gb = shutil.disk_usage(settings.data_dir).free / 1e9
    report(OK if free_gb >= 6 else WARN, f"Free disk: {free_gb:.1f} GB (models ~1-3 GB + videos)")
    cached = [p.name for p in settings.models_dir.glob("models--*")] if settings.models_dir.exists() else []
    report(OK if cached else WARN, f"Downloaded models: {', '.join(cached) if cached else 'none yet (prothom use e auto-download hobe, ba: python scripts/download_model.py)'}")

    with socket.socket() as s:
        busy = s.connect_ex(("127.0.0.1", 8000)) == 0
    report(WARN if busy else OK, "Port 8000 " + ("already use hocche (KOTHON_PORT=8001 ./run.sh)" if busy else "free"))

    if ffmpeg_ok and " ass " in filters:
        try:
            from app import video
            from app.subtitles import CaptionStyle, build_ass
            from app.transcript import Segment, distribute_words

            text = "বিশ্ব বাংলা ক্ষমতা র‍্যাব"
            segs = [Segment(1, 0.2, 1.8, text, distribute_words(text, 0.2, 1.8))]
            with tempfile.TemporaryDirectory() as td:
                td = Path(td)
                clip = td / "t.mp4"
                subprocess.run(
                    [settings.ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=540x960:rate=25:duration=2",
                     "-f", "lavfi", "-i", "sine=duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(clip)],
                    check=True,
                )
                info = video.probe(clip, settings.ffprobe)
                w, h = video.output_size(info.width, info.height)
                ass = build_ass(segs, CaptionStyle(), w, h, settings.watermark_text, info.duration)
                out = settings.data_dir / "doctor-sample.png"
                video.render_frame(clip, out, ass, info, 1.0, settings.fonts_dir, td / "w", settings.ffmpeg)
                video.render_video(clip, td / "o.mp4", ass, info, settings.fonts_dir, td / "w2", settings.ffmpeg)
            report(OK, f"Test render (caption burn-in) kaj korche. Sample dekho: {out}")
            print("     -> ei PNG ta kholo: Bangla juktakkhor (ক্ষ, শ্ব, ম্ব, র‍্যা) jora lagano thakle shob thik.")
        except Exception as e:  # noqa: BLE001
            report(FAIL, f"Test render fail: {e}")

    print()
    if failures:
        print(f"{failures} ta problem. Upore ✗ gulo fix koro.")
        return 1
    print("Shob thik ache. Chalao: ./run.sh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
