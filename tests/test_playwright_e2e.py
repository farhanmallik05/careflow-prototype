"""Headless Playwright visual and interaction regression test suite for CareFlow."""

import threading
import time
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright
import uvicorn

from careflow.api import create_app
from careflow.engine import Pathway
from careflow.persistence import User


PATHWAY = Pathway.load(Path(__file__).parent.parent / "careflow" / "pathways" / "screening_to_care.yaml")
HOST = "127.0.0.1"
PORT = 8991
BASE_URL = f"http://{HOST}:{PORT}"


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("playwright") / "careflow_playwright.sqlite"
    app = create_app(db_path=db_path, pathway=PATHWAY)
    store = app.state.store
    store.add_user(User("demo-coordinator", "Coordinator Sarah", "care_coordinator"))
    store.add_user(User("demo-specialist", "Dr. Specialist", "specialist"))

    config = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server to boot
    for _ in range(50):
        try:
            import urllib.request
            with urllib.request.urlopen(f"{BASE_URL}/health", timeout=0.5) as resp:
                if resp.status == 200:
                    break
        except Exception:
            time.sleep(0.1)

    yield BASE_URL
    server.should_exit = True
    thread.join(timeout=2.0)


def test_playwright_landing_and_login_flow(live_server):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # 1. Visit landing page
        page.goto(f"{live_server}/login")
        assert "CareFlow" in page.title()
        assert page.locator("header").is_visible()

        # 2. Perform signed demo login
        login_btn = page.locator("button:has-text('Demo Care Coordinator'), button:has-text('Sign in as demo-coordinator')")
        if login_btn.count() > 0:
            login_btn.first.click()
            page.wait_for_load_state("networkidle")

        browser.close()


def test_playwright_shortcuts_modal_toggle(live_server):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto(f"{live_server}/login")
        # Ensure modal starts hidden
        modal = page.locator("#shortcuts-modal")
        assert not modal.is_visible()

        # Press '?' key to open keyboard shortcuts modal
        page.keyboard.press("?")
        page.wait_for_timeout(200)
        assert modal.is_visible()
        assert "Navigator Keyboard Shortcuts" in page.text_content("#shortcuts-modal")

        # Press 'Escape' key to close modal
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        assert not modal.is_visible()

        browser.close()
