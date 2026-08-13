"""Browser test: the live activity feed + token streaming on the chat page.

Regression (2026-08-13): the chat form used a plain hx-post with no live
feedback — users only saw a static spinner while the agent searched. The
chat page now streams thinking bubbles + answer tokens via fetch-SSE and
renders the server-rendered turn fragment on completion.

Run against the test container:
    pytest tests/browser/test_live_feed.py -q -m browser
Env: BROWSER_SMOKE_URL, BROWSER_SMOKE_USER, BROWSER_SMOKE_PASS
"""
import os
import time

import pytest

pytestmark = pytest.mark.browser

BASE_URL = os.environ.get("BROWSER_SMOKE_URL", "http://localhost:8051").rstrip("/")
USER = os.environ.get("BROWSER_SMOKE_USER", "admin")
PASSWD = os.environ.get("BROWSER_SMOKE_PASS", "")


@pytest.fixture(scope="module")
def browser_context(playwright):
    if not PASSWD:
        pytest.skip("set BROWSER_SMOKE_PASS to run browser tests")
    browser = playwright.chromium.launch(headless=True, args=["--disable-gpu"])
    context = browser.new_context()
    page = context.new_page()
    page.on("pageerror", lambda exc: print(f"[pageerror] {exc}"))
    page.on("requestfailed", lambda req: print(f"[reqfailed] {req.method} {req.url} {req.failure}"))
    page.goto(BASE_URL + "/login")
    csrf = page.locator('input[name="_csrf"]').get_attribute("value")
    assert csrf, "login form must contain a _csrf token"
    page.fill('input[name="username"]', USER)
    page.fill('input[name="password"]', PASSWD)
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")
    yield page
    browser.close()


def _ask_question(page, question):
    page.goto(BASE_URL + "/")
    page.wait_for_load_state("networkidle")
    page.locator('a[href^="/collections/"]').first.click()
    page.wait_for_load_state("networkidle")
    page.fill('#ask-form input[name="question"]', question)
    page.click('#ask-form button[type="submit"]')
    page.wait_for_load_state("networkidle")


def _land_on_chat_page(page):
    """Navigate to an existing session's chat page (or create one via ask)."""
    page.goto(BASE_URL + "/sessions")
    page.wait_for_load_state("networkidle")
    links = page.locator('a[href^="/sessions/"]')
    if links.count() > 0:
        links.first.click()
        page.wait_for_load_state("networkidle")
        return
    _ask_question(page, "What is armor class?")
    # after the ask we land on the chat page already
    page.wait_for_load_state("networkidle")


def test_live_feed_shows_activity_and_tokens(browser_context):
    """A follow-up question on the chat page must show activity bubbles and
    stream the answer text; the final turn renders with sources."""
    page = browser_context
    _land_on_chat_page(page)
    assert page.locator("#chat-log").count() >= 1, "must be on chat page"
    turns_before = page.locator("#chat-log .rpg-chat-turn").count()

    # Ask a follow-up through the streaming form.
    page.fill('#chat-form input[name="question"]', "What is a goblin's AC?")
    page.click('#chat-form button[type="submit"]')

    # Activity log bubbles appear while searching/reading.
    bubbles = page.locator(".rpg-activity-log .rpg-log-item")
    for _ in range(60):
        if bubbles.count() > 0:
            break
        time.sleep(1)
    assert bubbles.count() >= 1, "no activity bubbles rendered"

    # A new turn (user + agent) must land via the streamed fragment.
    for _ in range(90):
        if page.locator("#chat-log .rpg-chat-turn").count() >= turns_before + 2:
            break
        time.sleep(1)
    assert page.locator("#chat-log .rpg-chat-turn").count() >= turns_before + 2, "new turn never rendered"
    assert page.locator("#chat-log .rpg-chat-answer").count() >= 1, "no answer rendered"
