"""ffmpeg / ffprobe wrappers: probe, audio extraction, caption burn-in, single-frame preview."""
from __future__ import annotations

import json
import logging
import math
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger("kothon.video")

MAX_LONG_SIDE = 1920  # output is scaled down so the long side is at most this


class VideoError(Exception):
    """Problem with the user's video. str(e) is safe to show to the user."""


class ToolError(Exception):
    """ffmpeg/ffprobe itself failed or is missing."""


@dataclass(frozen=True)
class VideoInfo:
    duration: float
    width: int  # as displayed (rotation already applied)
    height: int
    has_audio: bool
    rotation: int


def _run(cmd: list[str], timeout: float, cwd: Path | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=str(cwd) if cwd else None, stdin=subprocess.DEVNULL
        )
    except FileNotFoundError as e:
        raise ToolError(f"'{cmd[0]}' paoa jayni. `brew install ffmpeg` chalao.") from e
    except subprocess.TimeoutExpired as e:
        raise ToolError(f"'{cmd[0]}' timeout hoyeche") from e


def _rotation_of(stream: dict) -> int:
    rot = None
    tags = stream.get("tags") or {}
    if "rotate" in tags:
        try:
            rot = int(float(tags["rotate"]))
        except ValueError:
            rot = None
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                rot = int(round(float(sd["rotation"])))
            except (TypeError, ValueError):
                pass
    return (rot or 0) % 360


def probe(path: Path, ffprobe: str = "ffprobe") -> VideoInfo:
    r = _run([ffprobe, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)], 60)
    if r.returncode != 0:
        raise VideoError("Eta valid video file na (ba corrupt). MP4 / MOV diye abar try koro.")
    try:
        data = json.loads(r.stdout or "{}")
    except json.JSONDecodeError as e:
        raise VideoError("Video read kora gelo na.") from e
    streams = data.get("streams") or []
    vids = [s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")]
    if not vids:
        raise VideoError("Ei file e video nei. Video file upload koro.")
    v = vids[0]
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    try:
        w, h = int(v["width"]), int(v["height"])
    except (KeyError, TypeError, ValueError) as e:
        raise VideoError("Video er size jana gelo na.") from e
    rot = _rotation_of(v)
    if rot in (90, 270):
        w, h = h, w
    dur = None
    for cand in ((data.get("format") or {}).get("duration"), v.get("duration")):
        try:
            if cand not in (None, "N/A"):
                dur = float(cand)
                break
        except (TypeError, ValueError):
            continue
    if dur is None or not math.isfinite(dur) or dur <= 0:
        raise VideoError("Video er duration jana gelo na.")
    if w < 2 or h < 2:
        raise VideoError("Video er resolution onek chhoto.")
    return VideoInfo(duration=dur, width=w, height=h, has_audio=has_audio, rotation=rot)


def extract_audio(src: Path, dst: Path, ffmpeg: str = "ffmpeg") -> None:
    cmd = [ffmpeg, "-y", "-nostdin", "-v", "error", "-i", str(src), "-map", "0:a:0", "-vn", "-ac", "1",
           "-ar", "16000", "-c:a", "pcm_s16le", str(dst)]
    r = _run(cmd, 600)
    if r.returncode != 0 or not dst.exists() or dst.stat().st_size < 1000:
        log.error("audio extract failed: %s", (r.stderr or "")[-500:])
        raise VideoError("Video theke audio ber kora gelo na. Video te sound ache to?")


def output_size(width: int, height: int) -> tuple[int, int]:
    """Even dimensions, long side <= MAX_LONG_SIDE, aspect preserved."""
    scale = min(1.0, MAX_LONG_SIDE / max(width, height))
    w = max(2, int(round(width * scale / 2)) * 2)
    h = max(2, int(round(height * scale / 2)) * 2)
    return w, h


def _prepare_workdir(workdir: Path, ass_text: str, fonts_dir: Path) -> None:
    """Put captions.ass and a copy of the fonts next to each other so ffmpeg can use plain relative paths
    (no filtergraph escaping problems with spaces / colons in the user's folder names)."""
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "captions.ass").write_text(ass_text, encoding="utf-8")
    fd = workdir / "fonts"
    fd.mkdir(exist_ok=True)
    for f in fonts_dir.glob("*.ttf"):
        dst = fd / f.name
        if not dst.exists():
            shutil.copyfile(f, dst)


def _vf(out_w: int, out_h: int) -> str:
    return f"scale={out_w}:{out_h}:flags=lanczos,setsar=1,ass=captions.ass:fontsdir=fonts,format=yuv420p"


def render_video(
    src: Path,
    dst: Path,
    ass_text: str,
    info: VideoInfo,
    fonts_dir: Path,
    workdir: Path,
    ffmpeg: str = "ffmpeg",
    on_progress: Callable[[float], None] | None = None,
    timeout: float = 900,
) -> None:
    out_w, out_h = output_size(info.width, info.height)
    _prepare_workdir(workdir, ass_text, fonts_dir)
    tmp_out = workdir / "render_tmp.mp4"
    err_file = workdir / "ffmpeg_stderr.txt"
    cmd = [
        ffmpeg, "-y", "-nostdin", "-hide_banner", "-v", "error", "-progress", "pipe:1", "-nostats",
        "-i", str(src.resolve()),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", _vf(out_w, out_h),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        str(tmp_out.name),
    ]
    started = time.monotonic()
    with open(err_file, "wb") as ef:
        try:
            proc = subprocess.Popen(cmd, cwd=str(workdir), stdout=subprocess.PIPE, stderr=ef,
                                    stdin=subprocess.DEVNULL, text=True)
        except FileNotFoundError as e:
            raise ToolError("ffmpeg paoa jayni. `brew install ffmpeg` chalao.") from e
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if time.monotonic() - started > timeout:
                    proc.kill()
                    raise ToolError("Render onek beshi somoy nilo (timeout).")
                key, _, val = line.strip().partition("=")
                if key in ("out_time_us", "out_time_ms") and on_progress:
                    try:
                        sec = float(val) / 1_000_000.0  # both keys are microseconds in practice
                    except ValueError:
                        continue
                    on_progress(max(0.0, min(0.99, sec / info.duration)))
            proc.wait(timeout=60)
        except BaseException:
            proc.kill()
            proc.wait()
            raise
    if proc.returncode != 0 or not tmp_out.exists() or tmp_out.stat().st_size < 1000:
        tail = err_file.read_text(errors="replace")[-800:] if err_file.exists() else ""
        log.error("render failed (rc=%s): %s", proc.returncode, tail)
        raise ToolError("Video render fail korechhe. Terminal er log dekho.")
    os.replace(tmp_out, dst)
    if on_progress:
        on_progress(1.0)


def render_frame(
    src: Path,
    dst_png: Path,
    ass_text: str,
    info: VideoInfo,
    t: float,
    fonts_dir: Path,
    workdir: Path,
    ffmpeg: str = "ffmpeg",
) -> None:
    """Render exactly what the final video looks like at time t (single PNG)."""
    out_w, out_h = output_size(info.width, info.height)
    # preview at reduced size for speed; PlayRes stays correct because libass scales the script to the frame
    pw = min(out_w, 720 if out_w <= out_h else 1280)
    pw -= pw % 2
    ph = max(2, int(round(out_h * pw / out_w / 2)) * 2)
    _prepare_workdir(workdir, ass_text, fonts_dir)
    tmp_png = workdir / "preview_tmp.png"
    t = max(0.0, min(float(t), max(0.0, info.duration - 0.05)))
    vf = f"scale={pw}:{ph}:flags=bilinear,setsar=1,ass=captions.ass:fontsdir=fonts"
    # -ss AFTER -i on purpose: the ass filter must see the original timestamps.
    cmd = [ffmpeg, "-y", "-nostdin", "-hide_banner", "-v", "error", "-i", str(src.resolve()),
           "-ss", f"{t:.3f}", "-map", "0:v:0", "-vf", vf, "-frames:v", "1", "-update", "1", tmp_png.name]
    r = _run(cmd, 120, cwd=workdir)
    if r.returncode != 0 or not tmp_png.exists() or tmp_png.stat().st_size < 100:
        log.error("preview failed: %s", (r.stderr or "")[-600:])
        raise ToolError("Preview frame banano gelo na.")
    os.replace(tmp_png, dst_png)
