"""Kothon web API (FastAPI). Run with:  uvicorn app.main:create_app --factory"""
from __future__ import annotations

import dataclasses
import logging
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, video
from .config import DEFAULT_MODEL, MODEL_PRESETS, Settings
from .jobs import TRANSCRIBE_ACTIVE, JobStore, Runner, valid_job_id
from .subtitles import FONTS, PRESETS, SIZE_RATIO, CaptionStyle, build_ass, to_srt
from .transcribe import TranscribeError, Transcriber, WhisperTranscriber
from .transcript import apply_edits
from .video import ToolError, VideoError, VideoInfo

log = logging.getLogger("kothon")

ALLOWED_EXT = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".3gp": "video/3gpp",
}
CHUNK = 1024 * 1024


# ----------------------------------------------------------------------------- request models


class StyleIn(BaseModel):
    preset: Literal["reels", "classic", "box"] = "reels"
    position: Literal["bottom", "middle", "top"] = "bottom"
    size: Literal["small", "medium", "large"] = "medium"
    font: Literal["hind", "mina", "atma"] = "hind"
    words_per_caption: int = Field(3, ge=1, le=6)
    watermark: bool = True

    def to_style(self) -> CaptionStyle:
        names = {f.name for f in dataclasses.fields(CaptionStyle)}
        return CaptionStyle(**{k: v for k, v in self.model_dump().items() if k in names})


class PreviewIn(StyleIn):
    t: float = Field(0.0, ge=0, allow_inf_nan=False)


class SegmentIn(BaseModel):
    id: int = Field(-1, ge=-1, le=10_000_000)
    start: float = Field(allow_inf_nan=False, ge=0, le=86_400)
    end: float = Field(allow_inf_nan=False, ge=0, le=86_400)
    text: str = Field("", max_length=600)


class TranscriptIn(BaseModel):
    segments: list[SegmentIn] = Field(max_length=400)


# ----------------------------------------------------------------------------- helpers


def _clean_filename(name: str | None) -> str:
    name = Path(name or "video").name
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    return (name or "video")[:80]


def _public(job: dict) -> dict:
    keys = ("id", "stage", "progress", "message", "error", "filename", "model", "duration", "width", "height", "render")
    return {k: job.get(k) for k in keys}


def _info(job: dict) -> VideoInfo:
    return VideoInfo(
        duration=float(job["duration"]),
        width=int(job["width"]),
        height=int(job["height"]),
        has_audio=bool(job.get("has_audio", True)),
        rotation=int(job.get("rotation", 0)),
    )


def _snap_time(segments, t: float) -> float:
    """Preview time that is guaranteed to show a caption when there is any."""
    if not segments:
        return t
    for s in segments:
        if s.start <= t <= s.end:
            return t
    nearest = min(segments, key=lambda s: min(abs(t - s.start), abs(t - s.end)))
    return (nearest.start + nearest.end) / 2


# ----------------------------------------------------------------------------- app factory


def create_app(settings: Settings | None = None, transcriber: Transcriber | None = None) -> FastAPI:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = settings or Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    store = JobStore(settings.jobs_dir, settings.job_ttl_hours)
    runner = Runner()
    transcriber = transcriber or WhisperTranscriber(settings)
    store.cleanup_expired()

    app = FastAPI(title="Kothon", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.store = store
    app.state.runner = runner

    def _janitor() -> None:
        while True:
            time.sleep(1800)
            try:
                n = store.cleanup_expired()
                if n:
                    log.info("cleaned %d expired jobs", n)
            except Exception:
                log.exception("janitor failed")

    threading.Thread(target=_janitor, name="kothon-janitor", daemon=True).start()

    # ---- reject oversize uploads before the body is read
    @app.middleware("http")
    async def limit_upload(request: Request, call_next):
        if request.method == "POST" and request.url.path == "/api/jobs":
            cl = request.headers.get("content-length")
            if cl and cl.isdigit() and int(cl) > settings.max_upload_bytes + 2 * CHUNK:
                return JSONResponse({"detail": f"File onek boro. Maximum {settings.max_upload_mb} MB."}, status_code=413)
        return await call_next(request)

    def get_job(job_id: str) -> dict:
        if not valid_job_id(job_id):
            raise HTTPException(404, "Job paoa jayni.")
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404, "Job paoa jayni (hoyto expire hoye geche). Video abar upload koro.")
        return job

    def require_ready(job_id: str) -> dict:
        job = get_job(job_id)
        if job["stage"] != "ready":
            raise HTTPException(409, "Transcript ekhono ready na.")
        return job

    # ------------------------------------------------------------------ pages / config

    @app.get("/")
    def index():
        return FileResponse(settings.static_dir / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/api/config")
    def config():
        return {
            "version": __version__,
            "max_seconds": settings.max_seconds,
            "max_upload_mb": settings.max_upload_mb,
            "default_model": DEFAULT_MODEL,
            "models": [{"key": m.key, "label": m.label, "note": m.note} for m in MODEL_PRESETS.values()],
            "presets": list(PRESETS),
            "fonts": list(FONTS),
            "sizes": list(SIZE_RATIO),
            "watermark_text": settings.watermark_text,
            "formats": sorted(ALLOWED_EXT),
        }

    @app.get("/api/health")
    def health():
        return {"ok": True, "queue": runner.pending()}

    # ------------------------------------------------------------------ create job

    @app.post("/api/jobs", status_code=201)
    def create_job(file: UploadFile = File(...), model: str = Form(DEFAULT_MODEL)):
        if model not in MODEL_PRESETS:
            raise HTTPException(400, "Unknown model.")
        filename = _clean_filename(file.filename)
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_EXT:
            raise HTTPException(415, f"Ei format support kore na. Use koro: {', '.join(sorted(ALLOWED_EXT))}")

        job = store.create(filename=filename, model=model, source=f"source{ext}", message="Upload sesh. Queue e ache...")
        job_id = job["id"]
        d = store.dir(job_id)
        src = d / f"source{ext}"
        try:
            size = 0
            with open(src, "wb") as out:
                while True:
                    chunk = file.file.read(CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > settings.max_upload_bytes:
                        raise HTTPException(413, f"File onek boro. Maximum {settings.max_upload_mb} MB.")
                    out.write(chunk)
            if size == 0:
                raise HTTPException(422, "File ta khali.")
            info = video.probe(src, settings.ffprobe)
            if info.duration > settings.max_seconds + 0.5:
                raise HTTPException(
                    422, f"Video onek lamba ({info.duration:.0f}s). Ei version e maximum {settings.max_seconds:.0f} second."
                )
            if not info.has_audio:
                raise HTTPException(422, "Ei video te audio nei, tai caption banano jabe na.")
        except VideoError as e:
            store.delete(job_id)
            raise HTTPException(422, str(e))
        except ToolError as e:
            store.delete(job_id)
            raise HTTPException(500, str(e))
        except HTTPException:
            store.delete(job_id)
            raise
        except Exception:
            store.delete(job_id)
            log.exception("upload failed")
            raise HTTPException(500, "Upload e somossa hoyeche.")

        store.update(
            job_id,
            duration=info.duration,
            width=info.width,
            height=info.height,
            rotation=info.rotation,
            has_audio=info.has_audio,
            size=size,
        )

        def run_transcription() -> None:
            j = store.get(job_id)
            if j is None:
                return
            store.update(job_id, stage="extracting", progress=0.0, message="Audio ber kora hocche...")
            audio = d / "audio.wav"
            video.extract_audio(src, audio, settings.ffmpeg)

            def on_stage(stage: str, msg: str) -> None:
                store.update(job_id, stage=stage, message=msg, progress=0.0)

            def on_progress(p: float) -> None:
                store.update(job_id, persist=False, progress=round(float(p), 1))

            segments = transcriber.transcribe(audio, j["model"], float(j["duration"]), on_stage, on_progress)
            store.save_segments(job_id, segments)
            store.update(job_id, stage="ready", progress=100.0, message="", error=None)
            audio.unlink(missing_ok=True)

        def on_error(e: BaseException) -> None:
            msg = str(e) if isinstance(e, (VideoError, TranscribeError, ToolError)) else "Kichhu ekta vul hoyeche. Abar try koro."
            store.update(job_id, stage="failed", error=msg, message="", progress=0.0)

        runner.submit(run_transcription, on_error)
        return _public(store.get(job_id) or job)

    # ------------------------------------------------------------------ read job

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str):
        return _public(get_job(job_id))

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: str):
        job = get_job(job_id)
        if job["stage"] in TRANSCRIBE_ACTIVE or job["render"]["state"] in ("queued", "running"):
            raise HTTPException(409, "Kaj cholche, ektu pore delete koro.")
        store.delete(job_id)
        return {"ok": True}

    @app.get("/api/jobs/{job_id}/source")
    def job_source(job_id: str):
        job = get_job(job_id)
        path = store.dir(job_id) / job["source"]
        if not path.exists():
            raise HTTPException(404, "Video file nei.")
        return FileResponse(path, media_type=ALLOWED_EXT.get(path.suffix.lower(), "application/octet-stream"))

    # ------------------------------------------------------------------ transcript

    @app.get("/api/jobs/{job_id}/transcript")
    def get_transcript(job_id: str):
        require_ready(job_id)
        segs = store.load_segments(job_id)
        return {"segments": [{"id": s.id, "start": round(s.start, 2), "end": round(s.end, 2), "text": s.text} for s in segs]}

    @app.put("/api/jobs/{job_id}/transcript")
    def put_transcript(job_id: str, body: TranscriptIn):
        job = require_ready(job_id)
        try:
            segs = apply_edits(store.load_segments(job_id), [s.model_dump() for s in body.segments], float(job["duration"]))
        except ValueError as e:
            raise HTTPException(422, str(e))
        store.save_segments(job_id, segs)
        return {"ok": True, "count": len(segs)}

    @app.get("/api/jobs/{job_id}/download/srt")
    def download_srt(job_id: str):
        require_ready(job_id)
        content = to_srt(store.load_segments(job_id))
        stem = Path(get_job(job_id)["filename"]).stem or "captions"
        return Response(
            content=content.encode("utf-8"),
            media_type="application/x-subrip; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="captions.srt"; filename*=UTF-8\'\'{_q(stem)}.srt'},
        )

    # ------------------------------------------------------------------ preview + render

    def _build(job: dict, job_id: str, style: CaptionStyle) -> str:
        info = _info(job)
        out_w, out_h = video.output_size(info.width, info.height)
        return build_ass(store.load_segments(job_id), style, out_w, out_h, settings.watermark_text, info.duration)

    @app.post("/api/jobs/{job_id}/preview")
    def preview(job_id: str, body: PreviewIn):
        job = require_ready(job_id)
        segs = store.load_segments(job_id)
        ass = _build(job, job_id, body.to_style())
        work = store.dir(job_id) / f"preview_{uuid.uuid4().hex[:8]}"
        png = work / "frame.png"
        try:
            video.render_frame(
                store.dir(job_id) / job["source"], png, ass, _info(job), _snap_time(segs, body.t),
                settings.fonts_dir, work, settings.ffmpeg,
            )
            data = png.read_bytes()
        except (ToolError, VideoError) as e:
            raise HTTPException(500, str(e))
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return Response(content=data, media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.post("/api/jobs/{job_id}/render", status_code=202)
    def start_render(job_id: str, body: StyleIn):
        job = require_ready(job_id)
        if job["render"]["state"] in ("queued", "running"):
            raise HTTPException(409, "Render already cholche.")
        if not store.load_segments(job_id):
            raise HTTPException(422, "Kono caption nei. Age kisu caption add koro.")
        style = body.to_style()
        store.update_render(job_id, state="queued", progress=0.0, error=None)

        def run_render() -> None:
            j = store.get(job_id)
            if j is None:
                return
            store.update_render(job_id, state="running", progress=0.0)
            d = store.dir(job_id)
            video.render_video(
                d / j["source"], d / "output.mp4", _build(j, job_id, style), _info(j), settings.fonts_dir,
                d / "work", settings.ffmpeg,
                on_progress=lambda p: store.update_render(job_id, persist=False, progress=round(p * 100, 1)),
                timeout=settings.render_timeout_s,
            )
            store.update_render(job_id, state="done", progress=100.0, error=None)

        def on_error(e: BaseException) -> None:
            msg = str(e) if isinstance(e, (VideoError, ToolError)) else "Render e somossa hoyeche. Abar try koro."
            store.update_render(job_id, state="failed", error=msg, progress=0.0)

        runner.submit(run_render, on_error)
        return _public(store.get(job_id))

    @app.get("/api/jobs/{job_id}/download/video")
    def download_video(job_id: str):
        job = get_job(job_id)
        path = store.dir(job_id) / "output.mp4"
        if job["render"]["state"] != "done" or not path.exists():
            raise HTTPException(409, "Video ekhono ready na.")
        return FileResponse(path, media_type="video/mp4", filename="kothon-captioned.mp4")

    app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")
    app.mount("/fonts", StaticFiles(directory=str(settings.fonts_dir)), name="fonts")
    return app


def _q(s: str) -> str:
    from urllib.parse import quote

    return quote(s, safe="")
