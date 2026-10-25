"""Browser end-to-end test (optional, needs `pip install playwright` + a Chromium).

    python tests/e2e_ui.py [path/to/chromium]

Starts the real app with a fake transcriber (no model needed) and clicks through the UI.
"""
from __future__ import annotations

import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import uvicorn  # noqa: E402
from playwright.sync_api import expect, sync_playwright  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from conftest import FakeTranscriber, make_video  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="kothon-e2e-"))
    shot_dir = Path(os.environ.get("E2E_SHOTS", tmp))
    shot_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(data_dir=tmp / "data", fonts_dir=ROOT / "fonts", static_dir=ROOT / "static", max_seconds=20, max_upload_mb=50, cpu_threads=2)
    app = create_app(settings, FakeTranscriber())
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    base = f"http://127.0.0.1:{port}"
    video = make_video(tmp / "clip.mp4", 6, "360x640")
    exe = sys.argv[1] if len(sys.argv) > 1 else None
    errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox", "--autoplay-policy=no-user-gesture-required"])
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base)
        expect(page.locator("#limits")).to_contain_text("20")
        page.screenshot(path=str(shot_dir / "1-upload.png"))

        # wrong extension is rejected client-side
        bad = tmp / "notes.txt"
        bad.write_text("hi")
        page.set_input_files("#file", str(bad))
        expect(page.locator("#error")).to_be_visible()
        page.click("#error-close")

        page.set_input_files("#file", str(video))
        expect(page.locator("#view-editor")).to_be_visible(timeout=30000)
        rows = page.locator("#segs .seg")
        expect(rows).to_have_count(3)
        expect(rows.nth(0).locator("textarea")).to_have_value("আমি বাংলায় কথা বলছি")
        page.screenshot(path=str(shot_dir / "2-editor.png"), full_page=True)

        # edit text -> autosave
        rows.nth(0).locator("textarea").fill("আমি বাংলায় কথা বলছি না")
        expect(page.locator("#save-state")).to_contain_text("সেভ হয়েছে", timeout=5000)

        # edit time
        t_end = rows.nth(1).locator('input[data-k="end"]')
        t_end.fill("3.1")
        t_end.press("Tab")
        expect(page.locator("#save-state")).to_contain_text("সেভ হয়েছে", timeout=5000)

        # invalid time -> server error shown, not silent
        bad_end = rows.nth(1).locator('input[data-k="end"]')
        bad_end.fill("0.0")
        bad_end.press("Tab")
        expect(page.locator("#error")).to_be_visible(timeout=5000)
        page.click("#error-close")
        # fix it again
        rows.nth(1).locator('input[data-k="end"]').fill("2.8")
        rows.nth(1).locator('input[data-k="end"]').press("Tab")
        expect(page.locator("#save-state")).to_contain_text("সেভ হয়েছে", timeout=5000)

        # add caption at current time, type text
        page.evaluate("document.querySelector('#video').currentTime = 5.0")
        page.click("#btn-add")
        expect(page.locator("#segs .seg")).to_have_count(4)
        page.keyboard.type("হাতে লেখা ক্যাপশন")
        expect(page.locator("#save-state")).to_contain_text("সেভ হয়েছে", timeout=5000)

        # delete the third (conjunct test) segment
        page.locator("#segs .seg").nth(2).locator('button[data-act="del"]').click()
        expect(page.locator("#segs .seg")).to_have_count(3)
        expect(page.locator("#save-state")).to_contain_text("সেভ হয়েছে", timeout=5000)

        # live overlay follows the video time
        page.evaluate("document.querySelector('#video').currentTime = 0.5")
        expect(page.locator("#overlay")).to_be_visible(timeout=3000)
        expect(page.locator("#overlay")).to_contain_text("আমি বাংলায়")

        # exact frame preview from the server
        page.select_option("#s-preset", "box")
        page.select_option("#s-font", "mina")
        page.click("#btn-preview")
        expect(page.locator("#frame")).to_be_visible(timeout=20000)
        assert page.evaluate("document.querySelector('#frame').naturalWidth") > 100
        page.screenshot(path=str(shot_dir / "3-preview.png"), full_page=True)

        # render + download
        page.click("#btn-render")
        expect(page.locator("#btn-download")).to_be_visible(timeout=60000)
        href = page.get_attribute("#btn-download", "href")
        r = ctx.request.get(base + href)
        assert r.status == 200 and r.headers["content-type"] == "video/mp4" and len(r.body()) > 5000
        srt = ctx.request.get(base + page.get_attribute("#btn-srt", "href"))
        assert srt.status == 200 and "আমি বাংলায় কথা বলছি না" in srt.text() and "হাতে লেখা ক্যাপশন" in srt.text()
        page.screenshot(path=str(shot_dir / "4-rendered.png"), full_page=True)

        # reload -> resumes the same job, edits + render state intact
        page.reload()
        expect(page.locator("#view-editor")).to_be_visible(timeout=15000)
        expect(page.locator("#segs .seg")).to_have_count(3)
        expect(page.locator("#btn-download")).to_be_visible()
        expect(page.locator("#segs .seg").nth(0).locator("textarea")).to_have_value("আমি বাংলায় কথা বলছি না")
        # after reload the player points at the server copy (Playwright's Chromium has no H.264, so we only check the URL)
        assert page.evaluate("document.querySelector('#video').src").endswith("/source")

        # new video resets everything
        page.click("#btn-new")
        expect(page.locator("#view-upload")).to_be_visible()

        # mobile layout smoke test
        page.set_viewport_size({"width": 390, "height": 800})
        assert page.evaluate("document.documentElement.scrollWidth") <= 392, "horizontal overflow on mobile"
        browser.close()

    shutil.copytree(shot_dir, ROOT / "e2e-shots", dirs_exist_ok=True) if os.environ.get("E2E_KEEP") else None
    # the one deliberate 422 comes from the invalid-time step above
    real_errors = [e for e in errors if "422" not in e]
    if real_errors:
        print("BROWSER ERRORS:", real_errors)
        return 1
    print("E2E UI OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
