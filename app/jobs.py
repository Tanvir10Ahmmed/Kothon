"""Tiny job system: on-disk job folders + one background worker thread (so the CPU is never overloaded)."""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .transcript import Segment

log = logging.getLogger("kothon.jobs")

JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
TRANSCRIBE_ACTIVE = ("queued", "extracting", "loading_model", "transcribing")


def new_job_id() -> str:
    return uuid.uuid4().hex


def valid_job_id(job_id: str) -> bool:
    return bool(JOB_ID_RE.match(job_id or ""))


class JobStore:
    def __init__(self, root: Path, ttl_hours: float = 24.0):
        self.root = root
        self.ttl = ttl_hours * 3600
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self.root.mkdir(parents=True, exist_ok=True)
        self._load_existing()

    # ---- paths
    def dir(self, job_id: str) -> Path:
        if not valid_job_id(job_id):
            raise ValueError("bad job id")
        return self.root / job_id

    # ---- persistence
    def _write(self, job: dict) -> None:
        d = self.dir(job["id"])
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "job.json.tmp"
        tmp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, d / "job.json")

    def _load_existing(self) -> None:
        for d in self.root.iterdir():
            f = d / "job.json"
            if not (d.is_dir() and valid_job_id(d.name) and f.exists()):
                continue
            try:
                job = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                log.warning("dropping unreadable job %s", d.name)
                shutil.rmtree(d, ignore_errors=True)
                continue
            changed = False
            if job.get("stage") in TRANSCRIBE_ACTIVE:
                job.update(stage="failed", error="Server restart hoyechhilo. Video abar upload koro.", progress=0)
                changed = True
            if (job.get("render") or {}).get("state") in ("queued", "running"):
                job["render"] = {"state": "failed", "progress": 0, "error": "Server restart hoyechhilo. Abar render koro."}
                changed = True
            self._jobs[job["id"]] = job
            if changed:
                self._write(job)

    # ---- API
    def create(self, **fields: Any) -> dict:
        with self._lock:
            now = time.time()
            job = {
                "id": fields.pop("id", None) or new_job_id(),
                "created_at": now,
                "updated_at": now,
                "stage": "queued",
                "progress": 0.0,
                "message": "",
                "error": None,
                "render": {"state": "idle", "progress": 0.0, "error": None},
            }
            job.update(fields)
            self._jobs[job["id"]] = job
            self._write(job)
            return json.loads(json.dumps(job))

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return json.loads(json.dumps(job)) if job else None

    def update(self, job_id: str, persist: bool = True, **fields: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.update(fields)
            job["updated_at"] = time.time()
            if persist:
                self._write(job)

    def update_render(self, job_id: str, persist: bool = True, **fields: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job["render"] = {**job.get("render", {}), **fields}
            job["updated_at"] = time.time()
            if persist:
                self._write(job)

    def delete(self, job_id: str) -> bool:
        with self._lock:
            existed = self._jobs.pop(job_id, None) is not None
        shutil.rmtree(self.dir(job_id), ignore_errors=True)
        return existed

    def cleanup_expired(self) -> int:
        cutoff = time.time() - self.ttl
        with self._lock:
            old = [
                j["id"]
                for j in self._jobs.values()
                if j.get("updated_at", 0) < cutoff
                and j.get("stage") not in TRANSCRIBE_ACTIVE
                and (j.get("render") or {}).get("state") not in ("queued", "running")
            ]
        for jid in old:
            self.delete(jid)
        return len(old)

    # ---- transcript files
    def save_segments(self, job_id: str, segments: list[Segment]) -> None:
        d = self.dir(job_id)
        tmp = d / "transcript.json.tmp"
        tmp.write_text(json.dumps([s.to_dict() for s in segments], ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, d / "transcript.json")

    def load_segments(self, job_id: str) -> list[Segment]:
        f = self.dir(job_id) / "transcript.json"
        if not f.exists():
            return []
        return [Segment.from_dict(d) for d in json.loads(f.read_text(encoding="utf-8"))]


class Runner:
    """Runs tasks one after another in a single daemon thread. A failing task never kills the thread."""

    def __init__(self) -> None:
        self._q: queue.Queue[tuple[Callable[[], None], Callable[[BaseException], None] | None]] = queue.Queue()
        self._t = threading.Thread(target=self._loop, name="kothon-worker", daemon=True)
        self._t.start()

    def submit(self, fn: Callable[[], None], on_error: Callable[[BaseException], None] | None = None) -> None:
        self._q.put((fn, on_error))

    def _loop(self) -> None:
        while True:
            fn, on_error = self._q.get()
            try:
                fn()
            except BaseException as e:  # noqa: BLE001
                log.exception("task crashed")
                if on_error:
                    try:
                        on_error(e)
                    except Exception:
                        log.exception("error handler crashed")
            finally:
                self._q.task_done()

    def pending(self) -> int:
        return self._q.qsize()

    def join(self) -> None:  # used by tests
        self._q.join()
