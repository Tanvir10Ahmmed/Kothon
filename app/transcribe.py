"""Speech -> Bangla segments using faster-whisper (CTranslate2, CPU, int8)."""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable, Protocol

from .config import MODEL_PRESETS, Settings
from .transcript import Segment, Word, resplit_segments, sanitize_words

log = logging.getLogger("kothon.transcribe")

StageCb = Callable[[str, str], None]  # (stage, human message)
ProgressCb = Callable[[float], None]  # 0..100


class TranscribeError(Exception):
    """Safe-to-show error message."""


class Transcriber(Protocol):
    def transcribe(
        self, audio_path: Path, model_key: str, duration: float, on_stage: StageCb, on_progress: ProgressCb
    ) -> list[Segment]: ...


def _drop_repetition_loops(segments: list[Segment]) -> list[Segment]:
    """Whisper sometimes loops the same sentence on silence/music. Keep at most 2 identical in a row."""
    out: list[Segment] = []
    for seg in segments:
        if len(out) >= 2 and out[-1].text == seg.text and out[-2].text == seg.text:
            continue
        out.append(seg)
    return out


def build_segments(raw: list[tuple[float, float, str, list[tuple[str, float, float]] | None]]) -> list[Segment]:
    """raw = [(start, end, text, [(word, start, end), ...] or None)] -> clean, subtitle-sized Segments."""
    segs: list[Segment] = []
    for start, end, text, words in raw:
        text = " ".join((text or "").split())
        if not text:
            continue
        start = max(0.0, float(start))
        end = max(float(end), start + 0.1)
        ws: list[Word] = []
        if words:
            ws = [Word(t.strip(), float(s), float(e)) for t, s, e in words if t and t.strip()]
        # words must reproduce the text, otherwise ignore them (Segment text is the source of truth)
        if ws and " ".join(w.text for w in ws).split() != text.split():
            ws = []
        ws = sanitize_words(ws, start, end) if ws else []
        segs.append(Segment(id=0, start=start, end=end, text=text, words=ws))
    segs = _drop_repetition_loops(segs)
    return resplit_segments(segs)


def pick_compute_type() -> str:
    """int8 is fastest on CPU, but not every CTranslate2 build supports it (e.g. some Intel-Mac wheels).
    Ask the library instead of guessing, and fall back to float32 (slower, more RAM, same quality)."""
    try:
        import ctranslate2

        supported = set(ctranslate2.get_supported_compute_types("cpu"))
    except Exception:  # noqa: BLE001 - never block model loading on this probe
        return "int8"
    for candidate in ("int8", "int8_float32", "float32"):
        if candidate in supported:
            return candidate
    return "default"


class WhisperTranscriber:
    """Loads models lazily and keeps one in memory (the last used)."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = threading.Lock()
        self._model = None
        self._model_key: str | None = None

    def _load(self, key: str, on_stage: StageCb):
        with self._lock:
            if self._model is not None and self._model_key == key:
                return self._model
            preset = MODEL_PRESETS.get(key)
            if preset is None:
                raise TranscribeError("Unknown model.")
            on_stage("loading_model", f"Model load hocche ({preset.label}). Prothombar download hote kichhu minute lagte pare.")
            try:
                from faster_whisper import WhisperModel
            except ImportError as e:  # pragma: no cover
                raise TranscribeError("faster-whisper install kora nei. setup.sh abar chalao.") from e
            self._model = None  # free RAM before loading another
            self.settings.models_dir.mkdir(parents=True, exist_ok=True)
            try:
                self._model = WhisperModel(
                    preset.repo,
                    device="cpu",
                    compute_type=pick_compute_type(),
                    cpu_threads=self.settings.cpu_threads,
                    download_root=str(self.settings.models_dir),
                )
            except Exception as e:
                log.exception("model load failed")
                raise TranscribeError(
                    "Model load/download hoyni. Internet connection check koro, tarpor abar try koro."
                ) from e
            self._model_key = key
            return self._model

    def transcribe(self, audio_path, model_key, duration, on_stage, on_progress):
        model = self._load(model_key, on_stage)
        preset = MODEL_PRESETS[model_key]
        on_stage("transcribing", "Bangla kotha cholche...")

        def run(word_timestamps: bool):
            segments_iter, _info = model.transcribe(
                str(audio_path),
                language="bn",
                task="transcribe",
                beam_size=preset.beam_size,
                temperature=[0.0, 0.2, 0.4],
                condition_on_previous_text=False,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
                word_timestamps=word_timestamps,
            )
            raw = []
            for s in segments_iter:
                words = None
                if word_timestamps and getattr(s, "words", None):
                    words = [(w.word, w.start, w.end) for w in s.words]
                raw.append((s.start, s.end, s.text, words))
                on_progress(min(99.0, 100.0 * float(s.end) / max(duration, 0.1)))
            return raw

        try:
            try:
                raw = run(True)
            except Exception:
                # Some fine-tuned conversions lack alignment heads -> word timestamps fail.
                log.warning("word timestamps failed, retrying without them", exc_info=True)
                raw = run(False)
        except Exception as e:
            log.exception("transcription failed")
            raise TranscribeError("Transcribe korte giye somossa hoyeche. Terminal log dekho.") from e
        return build_segments(raw)
