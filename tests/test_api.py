from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import video
from app.main import create_app

from conftest import FakeTranscriber, make_video, wait_for


@pytest.fixture()
def env(settings):
    fake = FakeTranscriber()
    app = create_app(settings, fake)
    return TestClient(app), app, fake, settings


def upload(client, path: Path, model="small", name=None):
    with open(path, "rb") as f:
        return client.post("/api/jobs", files={"file": (name or path.name, f, "video/mp4")}, data={"model": model})


def wait_stage(client, jid, stages, timeout=30):
    def check():
        j = client.get(f"/api/jobs/{jid}").json()
        return j if j["stage"] in stages else None

    return wait_for(check, timeout)


def test_index_config_health(env):
    c, *_ = env
    r = c.get("/")
    assert r.status_code == 200 and "কথন" in r.text
    cfg = c.get("/api/config").json()
    assert cfg["default_model"] in [m["key"] for m in cfg["models"]] and cfg["max_seconds"] == 10
    assert c.get("/api/health").json()["ok"] is True
    assert c.get("/static/app.js").status_code == 200
    assert c.get("/fonts/HindSiliguri-Bold.ttf").status_code == 200


def test_full_flow(env, tmp_path):
    c, app, fake, settings = env
    src = make_video(tmp_path / "clip.mp4", 5, "320x568")
    r = upload(c, src)
    assert r.status_code == 201, r.text
    jid = r.json()["id"]
    j = wait_stage(c, jid, {"ready", "failed"})
    assert j["stage"] == "ready" and j["width"] == 320 and j["height"] == 568 and j["filename"] == "clip.mp4"
    assert fake.calls == 1

    # transcript
    t = c.get(f"/api/jobs/{jid}/transcript").json()["segments"]
    assert [s["id"] for s in t] == [1, 2, 3] and t[0]["text"] == "আমি বাংলায় কথা বলছি"

    # edit one word, delete one segment, add one
    edited = [
        {"id": 1, "start": 0.2, "end": 1.4, "text": "আমি বাংলায় কথা বলছি না"},
        {"id": 3, "start": 3.0, "end": 4.6, "text": ""},  # deleted
        {"id": 77, "start": 2.0, "end": 3.0, "text": "নতুন ক্যাপশন"},
    ]
    assert c.put(f"/api/jobs/{jid}/transcript", json={"segments": edited}).json()["count"] == 2
    t2 = c.get(f"/api/jobs/{jid}/transcript").json()["segments"]
    assert [s["text"] for s in t2] == ["আমি বাংলায় কথা বলছি না", "নতুন ক্যাপশন"]

    # SRT
    srt = c.get(f"/api/jobs/{jid}/download/srt")
    assert srt.status_code == 200
    assert srt.text.startswith("1\n00:00:00,200 --> 00:00:01,400\nআমি বাংলায় কথা বলছি না\n")
    assert "attachment" in srt.headers["content-disposition"]

    # source supports range requests (needed by Safari's <video>)
    rng = c.get(f"/api/jobs/{jid}/source", headers={"Range": "bytes=0-99"})
    assert rng.status_code == 206 and len(rng.content) == 100

    # exact preview frame
    p = c.post(f"/api/jobs/{jid}/preview", json={"t": 0.8, "preset": "reels", "font": "mina"})
    assert p.status_code == 200 and p.content[:4] == b"\x89PNG"
    # preview must clean up after itself
    assert not list(settings.jobs_dir.joinpath(jid).glob("preview_*"))

    # download before render is refused
    assert c.get(f"/api/jobs/{jid}/download/video").status_code == 409

    # render
    r = c.post(f"/api/jobs/{jid}/render", json={"preset": "box", "position": "middle", "words_per_caption": 2})
    assert r.status_code == 202
    j = wait_for(lambda: (lambda x: x if x["render"]["state"] in ("done", "failed") else None)(c.get(f"/api/jobs/{jid}").json()), 60)
    assert j["render"]["state"] == "done", j
    out = c.get(f"/api/jobs/{jid}/download/video")
    assert out.status_code == 200 and out.headers["content-type"] == "video/mp4"
    f = tmp_path / "result.mp4"
    f.write_bytes(out.content)
    info = video.probe(f)
    assert (info.width, info.height, info.has_audio) == (320, 568, True)

    # re-render with different style works too (state machine resets)
    assert c.post(f"/api/jobs/{jid}/render", json={"watermark": False}).status_code == 202
    wait_for(lambda: c.get(f"/api/jobs/{jid}").json()["render"]["state"] == "done", 60)

    # delete cleans disk
    assert c.delete(f"/api/jobs/{jid}").status_code == 200
    assert c.get(f"/api/jobs/{jid}").status_code == 404
    assert not (settings.jobs_dir / jid).exists()


def test_upload_rejections(env, tmp_path):
    c, app, fake, settings = env
    # wrong extension
    junk = tmp_path / "x.exe"
    junk.write_bytes(b"MZ" * 100)
    assert upload(c, junk).status_code == 415
    # right extension, not a video
    fake_vid = tmp_path / "x.mp4"
    fake_vid.write_bytes(b"not video" * 500)
    r = upload(c, fake_vid)
    assert r.status_code == 422 and "video" in r.json()["detail"].lower()
    # empty file
    empty = tmp_path / "e.mp4"
    empty.write_bytes(b"")
    assert upload(c, empty).status_code == 422
    # too long (limit is 10s in the fixture)
    long = make_video(tmp_path / "long.mp4", 12, "160x120")
    r = upload(c, long)
    assert r.status_code == 422 and "lamba" in r.json()["detail"]
    # no audio track
    silent = make_video(tmp_path / "s.mp4", 2, "160x120", audio=False)
    r = upload(c, silent)
    assert r.status_code == 422 and "audio" in r.json()["detail"]
    # unknown model
    ok = make_video(tmp_path / "ok.mp4", 2, "160x120")
    assert upload(c, ok, model="nope").status_code == 400
    # nothing leaked on disk and transcriber never ran
    assert list(settings.jobs_dir.iterdir()) == []
    assert fake.calls == 0


def test_oversize_upload_rejected_by_header_and_by_stream(env, tmp_path):
    c, app, fake, settings = env
    r = c.post("/api/jobs", content=b"x", headers={"content-length": str(500 * 1024 * 1024), "content-type": "multipart/form-data; boundary=a"})
    assert r.status_code == 413
    big = tmp_path / "big.mp4"
    with open(big, "wb") as f:
        f.truncate(settings.max_upload_bytes + 10)
    assert upload(c, big).status_code == 413
    assert list(settings.jobs_dir.iterdir()) == []


def test_bad_ids_and_state_guards(env, tmp_path):
    c, *_ = env
    for bad in ["../etc/passwd", "nothex", "0" * 31, "g" * 32]:
        assert c.get(f"/api/jobs/{bad}").status_code == 404
    assert c.get(f"/api/jobs/{'0' * 32}").status_code == 404
    assert c.put(f"/api/jobs/{'0' * 32}/transcript", json={"segments": []}).status_code == 404


def test_transcript_validation_errors(env, tmp_path):
    c, *_ = env
    jid = upload(c, make_video(tmp_path / "a.mp4", 5, "160x120")).json()["id"]
    wait_stage(c, jid, {"ready"})
    url = f"/api/jobs/{jid}/transcript"
    assert c.put(url, json={"segments": [{"id": 1, "start": 2, "end": 2.0, "text": "ক"}]}).status_code == 422
    assert c.put(url, json={"segments": [{"id": 1, "start": 9, "end": 12, "text": "ক"}]}).status_code == 422
    assert c.put(url, json={"segments": [{"id": 1, "start": -1, "end": 2, "text": "ক"}]}).status_code == 422
    assert c.put(url, json={"segments": [{"id": 1, "start": 0, "end": 2, "text": "ক" * 700}]}).status_code == 422
    assert c.put(url, json={"segments": "nope"}).status_code == 422
    assert c.put(url, json={"segments": [{"id": 1, "start": "NaN", "end": 2, "text": "ক"}]}).status_code == 422
    # the stored transcript survives all those failures
    assert len(c.get(url).json()["segments"]) == 3


def test_render_guards(env, tmp_path):
    c, *_ = env
    jid = upload(c, make_video(tmp_path / "a.mp4", 5, "160x120")).json()["id"]
    wait_stage(c, jid, {"ready"})
    assert c.post(f"/api/jobs/{jid}/render", json={"preset": "nope"}).status_code == 422
    assert c.post(f"/api/jobs/{jid}/render", json={"words_per_caption": 99}).status_code == 422
    assert c.post(f"/api/jobs/{jid}/preview", json={"t": -1}).status_code == 422
    # empty transcript -> friendly error instead of a pointless render
    c.put(f"/api/jobs/{jid}/transcript", json={"segments": []})
    assert c.post(f"/api/jobs/{jid}/render", json={}).status_code == 422


def test_transcriber_failure_is_reported_and_server_survives(settings, tmp_path):
    app = create_app(settings, FakeTranscriber(fail=True))
    c = TestClient(app)
    jid = upload(c, make_video(tmp_path / "a.mp4", 3, "160x120")).json()["id"]
    j = wait_stage(c, jid, {"failed"})
    assert j["error"] == "fake failure"
    assert c.get(f"/api/jobs/{jid}/transcript").status_code == 409  # not ready
    # worker thread still alive: next job runs
    app.state.runner.join()
    app2 = create_app(settings, FakeTranscriber())
    c2 = TestClient(app2)
    jid2 = upload(c2, make_video(tmp_path / "b.mp4", 3, "160x120")).json()["id"]
    assert wait_stage(c2, jid2, {"ready", "failed"})["stage"] == "ready"


def test_unexpected_crash_in_transcriber_gives_generic_message(settings, tmp_path):
    class Boom:
        def transcribe(self, *a, **k):
            raise RuntimeError("secret internal detail /home/x")

    c = TestClient(create_app(settings, Boom()))
    jid = upload(c, make_video(tmp_path / "a.mp4", 3, "160x120")).json()["id"]
    j = wait_stage(c, jid, {"failed"})
    assert "secret" not in j["error"]


def test_empty_transcript_is_a_valid_result(settings, tmp_path):
    c = TestClient(create_app(settings, FakeTranscriber(empty=True)))
    jid = upload(c, make_video(tmp_path / "a.mp4", 3, "160x120")).json()["id"]
    wait_stage(c, jid, {"ready"})
    assert c.get(f"/api/jobs/{jid}/transcript").json()["segments"] == []
    # user can add a caption manually and render
    assert c.put(f"/api/jobs/{jid}/transcript", json={"segments": [{"id": 1, "start": 0.5, "end": 2, "text": "হাতে লেখা"}]}).status_code == 200
    assert c.post(f"/api/jobs/{jid}/render", json={}).status_code == 202
    wait_for(lambda: c.get(f"/api/jobs/{jid}").json()["render"]["state"] == "done", 60)


def test_jobs_survive_restart_and_interrupted_work_is_marked_failed(settings, tmp_path):
    c = TestClient(create_app(settings, FakeTranscriber()))
    jid = upload(c, make_video(tmp_path / "a.mp4", 3, "160x120")).json()["id"]
    wait_stage(c, jid, {"ready"})
    # simulate a crash in the middle of a render + a transcription
    store = c.app.state.store
    store.update_render(jid, state="running", progress=40)
    jid2 = store.create(filename="b.mp4", source="source.mp4", stage="transcribing", model="small")["id"]
    c2 = TestClient(create_app(settings, FakeTranscriber()))
    j = c2.get(f"/api/jobs/{jid}").json()
    assert j["stage"] == "ready" and j["render"]["state"] == "failed"
    assert c2.get(f"/api/jobs/{jid}/transcript").status_code == 200
    assert c2.get(f"/api/jobs/{jid2}").json()["stage"] == "failed"


def test_expired_jobs_are_cleaned(settings, tmp_path):
    app = create_app(settings, FakeTranscriber())
    c = TestClient(app)
    jid = upload(c, make_video(tmp_path / "a.mp4", 3, "160x120")).json()["id"]
    wait_stage(c, jid, {"ready"})
    store = app.state.store
    assert store.cleanup_expired() == 0
    store.update(jid, updated_at=time.time())  # touch
    store._jobs[jid]["updated_at"] = time.time() - 48 * 3600
    assert store.cleanup_expired() == 1
    assert c.get(f"/api/jobs/{jid}").status_code == 404 and not (settings.jobs_dir / jid).exists()


def test_filename_sanitised(env, tmp_path):
    c, *_ = env
    src = make_video(tmp_path / "ok.mp4", 2, "160x120")
    r = upload(c, src, name="../../evil\x00name <b>.mp4")
    assert r.status_code == 201
    assert "/" not in r.json()["filename"] and "\x00" not in r.json()["filename"]
