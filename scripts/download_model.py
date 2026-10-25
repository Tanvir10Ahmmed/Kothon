#!/usr/bin/env python3
"""Pre-download a model so the first caption job does not wait.   python scripts/download_model.py [bn-medium|large-v3|small]"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DEFAULT_MODEL, MODEL_PRESETS, Settings  # noqa: E402


def main() -> int:
    key = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MODEL
    if key not in MODEL_PRESETS:
        print(f"Unknown model '{key}'. Choose: {', '.join(MODEL_PRESETS)}")
        return 2
    preset = MODEL_PRESETS[key]
    settings = Settings.from_env()
    settings.models_dir.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {preset.repo} ({preset.note}) -> {settings.models_dir}")
    from faster_whisper.utils import download_model

    path = download_model(preset.repo, cache_dir=str(settings.models_dir))
    print("OK:", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
