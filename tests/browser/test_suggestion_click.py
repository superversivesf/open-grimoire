"""Regression: suggestion follow-up buttons must work in a real browser.

CSP + htmx regression (2026-08-11): the inline <script> on chat.html carried a
per-request CSP nonce, but htmx's body swap delivers a response whose script has
a DIFFERENT nonce → the browser blocked it → askFollowUp never defined → clicking
a suggestion button did nothing. Fixed by moving the handler to /static/chat.js
(script-src 'self') with document-level delegation.

Run against the test container:
    pytest tests/browser/test_suggestion_click.py -q -m browser
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


def test_suggestion_click_appends_turn(browser_context):
    """Clicking a suggestion button must submit a follow-up that appends a turn."""
    page = browser_context
    page.goto(BASE_URL + "/")
    page.wait_for_load_state("networkidle")
    col = page.locator('a[href^="/collections/"]').first
    col.click()
    page.wait_for_load_state("networkidle")
    page.fill('#ask-form input[name="question"]', "What is a goblin's AC?")
    page.click('#ask-form button[type="submit"]')
    page.wait_for_load_state("networkidle")

    btns = page.locator(".rpg-suggestion-btn")
    for _ in range(60):
        if btns.count() > 0:
            break
        time.sleep(1)
    assert btns.count() > 0, "no suggestion buttons rendered"

    turns_before = page.locator("#chat-log .rpg-chat-turn").count()
    btns.first.click()
    for _ in range(45):
        if page.locator("#chat-log .rpg-chat-turn").count() > turns_before:
            break
        time.sleep(1)
    turns_after = page.locator("#chat-log .rpg-chat-turn").count()
    assert turns_after > turns_before, "suggestion click did not append a turn"
