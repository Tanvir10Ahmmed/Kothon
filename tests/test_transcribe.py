"""Tests the Whisper wrapper without downloading a model (faster_whisper.WhisperModel is replaced by a stub)."""
from __future__ import annotations

import inspect
import sys
import types
from pathlib import Path

import pytest

from app.transcribe import TranscribeError, WhisperTranscriber


class W:
    def __init__(self, word, start, end):
        self.word, self.start, self.end = word, start, end


class S:
    def __init__(self, start, end, text, words):
        self.start, self.end, self.text, self.words = start, end, text, words


def install_stub(monkeypatch, *, fail_word_ts=False, fail_load=False, record=None):
    calls = record if record is not None else {}

    class StubModel:
        def __init__(self, repo, **kw):
            if fail_load:
                raise OSError("no internet")
            calls["init"] = (repo, kw)

        def transcribe(self, audio, **kw):
            calls.setdefault("transcribe", []).append(kw)
            if kw["word_timestamps"] and fail_word_ts:
                def bad():
                    raise RuntimeError("alignment heads missing")
                    yield  # pragma: no cover
                return bad(), None
            ws = [W(" আমি", 0.0, 0.5), W(" ভালো", 0.5, 1.0), W(" আছি", 1.0, 2.0)] if kw["word_timestamps"] else None
            return iter([S(0.0, 2.0, " আমি ভালো আছি", ws), S(3.0, 4.0, " দ্বিতীয় বাক্য", None)]), None

    mod = types.ModuleType("faster_whisper")
    mod.WhisperModel = StubModel
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    return calls


def run(settings, key="bn-medium", tmp=None):
    stages, progress = [], []
    t = WhisperTranscriber(settings)
    segs = t.transcribe(Path("a.wav"), key, 5.0, lambda s, m: stages.append(s), progress.append)
    return segs, stages, progress, t


def test_happy_path(settings, monkeypatch):
    calls = install_stub(monkeypatch)
    segs, stages, progress, t = run(settings)
    assert stages == ["loading_model", "transcribing"]
    assert [s.text for s in segs] == ["আমি ভালো আছি", "দ্বিতীয় বাক্য"]
    assert segs[0].words[1].text == "ভালো" and segs[0].words[1].start == pytest.approx(0.5)
    assert progress and progress[-1] <= 99
    repo, kw = calls["init"]
    assert repo == "SayedShaun/bengali-whisper-medium-ct2" and kw["device"] == "cpu" and kw["compute_type"] == "int8"
    assert kw["download_root"].endswith("models")
    tk = calls["transcribe"][0]
    assert tk["language"] == "bn" and tk["vad_filter"] is True and tk["condition_on_previous_text"] is False
    # model is cached between calls
    t.transcribe(Path("a.wav"), "bn-medium", 5.0, lambda *_: None, lambda *_: None)
    assert "init" in calls


def test_falls_back_when_word_timestamps_unsupported(settings, monkeypatch):
    calls = install_stub(monkeypatch, fail_word_ts=True)
    segs, *_ = run(settings)
    assert [s.text for s in segs] == ["আমি ভালো আছি", "দ্বিতীয় বাক্য"]
    assert all(s.words for s in segs)  # words were synthesised
    assert [c["word_timestamps"] for c in calls["transcribe"]] == [True, False]


def test_load_failure_gives_friendly_error(settings, monkeypatch):
    install_stub(monkeypatch, fail_load=True)
    with pytest.raises(TranscribeError) as e:
        run(settings)
    assert "Internet" in str(e.value) and "no internet" not in str(e.value)


def test_unknown_model(settings, monkeypatch):
    install_stub(monkeypatch)
    with pytest.raises(TranscribeError):
        run(settings, key="nope")


def test_kwargs_match_real_faster_whisper_signature():
    """Guards against typos / API drift: every kwarg we pass must exist in the installed faster-whisper."""
    faster_whisper = pytest.importorskip("faster_whisper")
    sig = inspect.signature(faster_whisper.WhisperModel.transcribe)
    for name in ("language", "task", "beam_size", "temperature", "condition_on_previous_text", "vad_filter",
                 "vad_parameters", "word_timestamps"):
        assert name in sig.parameters, name
    init = inspect.signature(faster_whisper.WhisperModel.__init__)
    for name in ("device", "compute_type", "cpu_threads", "download_root"):
        assert name in init.parameters, name


def _stub_ct2(monkeypatch, supported=None, boom=False):
    mod = types.ModuleType("ctranslate2")

    def get_supported_compute_types(device):
        if boom:
            raise RuntimeError("probe failed")
        return supported

    mod.get_supported_compute_types = get_supported_compute_types
    monkeypatch.setitem(sys.modules, "ctranslate2", mod)


@pytest.mark.parametrize(
    "supported, expected",
    [
        ({"int8", "float32"}, "int8"),
        ({"int8_float32", "float32"}, "int8_float32"),
        ({"float32"}, "float32"),  # e.g. a CTranslate2 build without int8 kernels
        (set(), "default"),
    ],
)
def test_pick_compute_type(monkeypatch, supported, expected):
    from app.transcribe import pick_compute_type

    _stub_ct2(monkeypatch, supported)
    assert pick_compute_type() == expected


def test_pick_compute_type_never_raises(monkeypatch):
    from app.transcribe import pick_compute_type

    _stub_ct2(monkeypatch, boom=True)
    assert pick_compute_type() == "int8"
