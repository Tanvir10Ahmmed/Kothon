"""Central configuration. Every value can be overridden with an environment variable."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class ModelPreset:
    key: str
    repo: str  # Hugging Face repo id (downloaded automatically on first use)
    label: str
    note: str
    beam_size: int


# Order matters: the first one is the default.
MODEL_PRESETS: dict[str, ModelPreset] = {
    "bn-medium": ModelPreset(
        key="bn-medium",
        repo="SayedShaun/bengali-whisper-medium-ct2",
        label="Bangla Accurate (Bengali fine-tuned medium)",
        note="~0.8 GB download, Bangla er jonno fine-tuned",
        beam_size=5,
    ),
    "large-v3": ModelPreset(
        key="large-v3",
        repo="Systran/faster-whisper-large-v3",
        label="Whisper large-v3 (best general, slow)",
        note="~3 GB download, onek slow kintu casual kothay valo hote pare",
        beam_size=5,
    ),
    "small": ModelPreset(
        key="small",
        repo="Systran/faster-whisper-small",
        label="Fast (small, less accurate)",
        note="~0.5 GB download, dhrut kintu bhul beshi",
        beam_size=3,
    ),
}
_env_default = os.environ.get("KOTHON_DEFAULT_MODEL", "")
DEFAULT_MODEL = _env_default if _env_default in MODEL_PRESETS else next(iter(MODEL_PRESETS))


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    fonts_dir: Path
    static_dir: Path
    max_seconds: float = 60.0
    max_upload_mb: int = 300
    job_ttl_hours: float = 24.0
    cpu_threads: int = 8
    watermark_text: str = "কথন দিয়ে তৈরি"
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    render_timeout_s: int = 900

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def max_upload_bytes(self) -> int:
        return int(self.max_upload_mb) * 1024 * 1024

    @classmethod
    def from_env(cls) -> "Settings":
        def _f(name: str, default: float) -> float:
            try:
                return float(os.environ.get(name, default))
            except ValueError:
                return float(default)

        cpu = os.cpu_count() or 4
        return cls(
            data_dir=Path(os.environ.get("KOTHON_DATA_DIR", BASE_DIR / "data")).expanduser().resolve(),
            fonts_dir=BASE_DIR / "fonts",
            static_dir=BASE_DIR / "static",
            max_seconds=_f("KOTHON_MAX_SECONDS", 60),
            max_upload_mb=int(_f("KOTHON_MAX_UPLOAD_MB", 300)),
            job_ttl_hours=_f("KOTHON_JOB_TTL_HOURS", 24),
            cpu_threads=int(_f("KOTHON_THREADS", min(8, cpu))),
            watermark_text=os.environ.get("KOTHON_WATERMARK", "কথন দিয়ে তৈরি"),
            ffmpeg=os.environ.get("KOTHON_FFMPEG", "ffmpeg"),
            ffprobe=os.environ.get("KOTHON_FFPROBE", "ffprobe"),
        )
