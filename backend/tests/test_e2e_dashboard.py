"""End-to-end: the built dashboard driven in a real browser against demo mode.

Skipped when Playwright or a Chromium binary is not available.
Run alone with:  python -m pytest backend/tests/test_e2e_dashboard.py
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
playwright = pytest.importorskip("playwright.sync_api")

CHROMIUM_CANDIDATES = [
    os.environ.get("CHROMIUM_PATH", ""),
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    shutil.which("chromium") or "",
    shutil.which("google-chrome") or "",
]
CHROMIUM = next((p for p in CHROMIUM_CANDIDATES if p and Path(p).exists()), None)
pytestmark = [
    pytest.mark.skipif(CHROMIUM is None, reason="no Chromium binary"),
    pytest.mark.skipif(not (ROOT / "frontend" / "dist" / "index.html").exists(), reason="dashboard not built"),
]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    port = _free_port()
    db = tmp_path_factory.mktemp("e2e") / "e2e.db"
    env = {
        **os.environ,
        "DEMO_MODE": "true",
        "PORT": str(port),
        "MONITOR_INTERVAL_SECONDS": "0",
        "DATABASE_URL": f"sqlite+aiosqlite:///{db}",
        "TELEGRAM_BOT_TOKEN": "",
    }
    proc = subprocess.Popen([sys.executable, "run.py", "--no-browser"], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            demo = json.load(urllib.request.urlopen(f"{base}/api/health", timeout=1))["demo"]
            break
        except OSError:
            time.sleep(0.5)
    else:
        proc.kill()
        pytest.fail("server did not start: " + proc.stdout.read().decode()[-2000:])
    yield base, demo
    proc.terminate()
    proc.wait(10)


@pytest.fixture
def page(server):
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROMIUM)
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        ctx.add_init_script("localStorage.setItem('ct-lang','en')")
        pg = ctx.new_page()
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: m.type == "error" and errors.append(m.text))
        pg.set_default_timeout(15000)
        yield pg
        assert errors == [], f"browser errors: {errors}"
        browser.close()


def test_search_to_wallet_and_tabs(server, page):
    base, demo = server
    page.goto(base)
    # Paste and submit immediately, before chain detection has answered.
    search = page.locator("main").get_by_placeholder("Wallet address")
    search.fill(demo["scammer"])
    search.press("Enter")
    page.wait_for_url(f"**/wallet/tron/{demo['scammer']}")
    page.get_by_text("Total value").wait_for()
    page.get_by_text("Pass-through wallet").first.wait_for()

    page.get_by_role("tab", name="Counterparties").click()
    page.get_by_text("Received from").wait_for()
    assert page.locator("tbody tr").count() >= 14  # 14 victims + mules

    page.get_by_role("tab", name="Transfers").click()
    page.locator("main table").get_by_role("link", name="Trace").first.wait_for()

    page.get_by_role("tab", name="Graph").click()
    page.get_by_text("wallets ·").wait_for()
    assert page.locator("canvas").count() > 0

    page.get_by_role("tab", name="Risk").click()
    page.get_by_text("Many senders in a short time").wait_for()


def test_trace_and_save_to_case(server, page):
    base, demo = server
    page.goto(f"{base}/wallet/tron/{demo['victim']}?tab=transfers")
    page.locator("main table").get_by_role("link", name="Trace").first.click()
    page.get_by_text("Where the money ended").wait_for()
    page.get_by_text("Exchange / service").first.wait_for()

    page.get_by_role("button", name="Save to case").click()
    page.get_by_placeholder("Title").fill("E2E case")
    page.get_by_role("dialog").get_by_role("button", name="Save").click()
    page.get_by_role("dialog").wait_for(state="detached")

    page.goto(f"{base}/cases")
    page.get_by_text("E2E case").click()
    page.get_by_text("Traces (1)").wait_for()
    page.get_by_role("button", name="Add note").wait_for()
    page.get_by_placeholder("Note").fill("victim reported on Monday")
    page.get_by_role("button", name="Add note").click()
    page.get_by_text("victim reported on Monday").wait_for()


def test_watchlist_label_and_language(server, page):
    base, demo = server
    page.goto(f"{base}/wallet/tron/{demo['mule']}")
    page.get_by_role("button", name="Watch").click()
    page.get_by_role("button", name="Watching").wait_for()

    page.get_by_role("button", name="Label").click()
    page.get_by_role("dialog").get_by_label("Name").fill("Mule A (e2e)")
    page.get_by_role("dialog").get_by_role("button", name="Save").click()
    page.get_by_text("Mule A (e2e)").first.wait_for()

    page.goto(f"{base}/watchlist")
    page.get_by_role("button", name="Check now").click()
    page.get_by_text(demo["mule"][:6]).first.wait_for()

    page.goto(f"{base}/labels")
    page.get_by_label("Search addresses...").fill("e2e")
    page.get_by_text("Mule A (e2e)").wait_for()

    page.get_by_role("button", name="Language").click()
    page.get_by_role("heading", name="برچسب آدرس‌ها").wait_for()
    assert page.evaluate("document.documentElement.dir") == "rtl"


def test_links_between_wallets(server, page):
    from app.providers import demo as story

    base, _ = server
    page.goto(f"{base}/links")
    members = [story.SCAMMER, *story.MULES, story.VICTIMS[0], story.VICTIMS[1]]
    page.locator("main textarea").fill("\n".join(members))
    page.get_by_text("(7 addresses)").wait_for()
    page.get_by_role("button", name="Analyze links").click()
    page.get_by_text("Findings").wait_for()
    page.get_by_text(f"went from #1 ({story.SCAMMER[:5]}").first.wait_for()
    page.get_by_text("Direct transfers between wallets (").wait_for()
    assert page.locator("canvas").count() > 0
    assert "job=" in page.url
    # Reloading keeps the finished analysis.
    page.reload()
    page.get_by_text("Findings").wait_for()


def test_mobile_layout_has_no_horizontal_scroll(server, page):
    base, demo = server
    page.set_viewport_size({"width": 375, "height": 800})
    for path in ["/", f"/wallet/tron/{demo['scammer']}", "/trace", "/links", "/cases", "/watchlist", "/labels", "/settings"]:
        page.goto(base + path)
        page.wait_for_timeout(800)
        width = page.evaluate("document.documentElement.scrollWidth")
        assert width <= 375, f"{path} overflows horizontally ({width}px)"
