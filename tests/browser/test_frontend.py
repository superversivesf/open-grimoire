"""Browser-based end-to-end tests against a live Open Grimoire frontend.

Runs the REAL frontend in a headless Chromium via Playwright — exercises the
browser behavior HTTP tests cannot: HTMX swaps, CSP nonce enforcement, the
PDF viewer iframe, cookie handling, and the full click-through journey.

Run against the test container (default):
    pytest tests/browser/test_frontend.py -v -m browser

Env vars:
    BROWSER_SMOKE_URL=      # default http://localhost:8051
    BROWSER_SMOKE_USER=     # default admin
    BROWSER_SMOKE_PASS=     # required
    BROWSER_HEADED=1        # run with a visible browser (debugging)
"""
import os
import re
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
    browser = playwright.chromium.launch(
        headless=os.environ.get("BROWSER_HEADED") != "1",
        args=["--disable-gpu"],
    )
    context = browser.new_context()
    page = context.new_page()
    # Surface console errors and failed requests for diagnosis.
    page.on("console", lambda msg: print(f"[console:{msg.type}] {msg.text}"))
    page.on("pageerror", lambda exc: print(f"[pageerror] {exc}"))
    page.on("requestfailed", lambda req: print(f"[reqfailed] {req.method} {req.url} {req.failure}"))
    page.goto(BASE_URL + "/login")
    # Extract the CSRF token from the form, submit login.
    csrf = page.locator('input[name="_csrf"]').get_attribute("value")
    assert csrf, "login form must contain a _csrf token"
    page.fill('input[name="username"]', USER)
    page.fill('input[name="password"]', PASSWD)
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")
    yield page
    browser.close()


def _first_collection_link(page) -> str:
    """Return the href of the first collection card on the library page."""
    page.goto(BASE_URL + "/")
    page.wait_for_load_state("networkidle")
    link = page.locator('a[href^="/collections/"][href*="table" i]').first
    # Collection cards link to /collections/{id}; the table link is a child.
    cards = page.locator("div.rpg-collection-card a")
    n = cards.count()
    for i in range(n):
        href = cards.nth(i).get_attribute("href") or ""
        m = re.match(r"^/collections/([a-f0-9]{32})$", href)
        if m:
            return href
    return ""


# ─── Login & auth flow ────────────────────────────────────────────────

def test_login_lands_in_library(browser_context):
    page = browser_context
    assert page.url.rstrip("/").endswith("/") or "/" in page.url
    assert "Open Grimoire" in page.content()


def test_logout_and_relogin(browser_context):
    page = browser_context
    page.goto(BASE_URL + "/")
    page.wait_for_load_state("networkidle")
    page.click('form[action="/logout"] button')
    page.wait_for_load_state("networkidle")
    assert "/login" in page.url
    # Back on the login page, re-login.
    csrf = page.locator('input[name="_csrf"]').get_attribute("value")
    page.fill('input[name="username"]', USER)
    page.fill('input[name="password"]', PASSWD)
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")
    assert "Open Grimoire" in page.content()


# ─── Library & navigation ─────────────────────────────────────────────

def test_library_shows_collections(browser_context):
    page = browser_context
    page.goto(BASE_URL + "/")
    page.wait_for_load_state("networkidle")
    cards = page.locator("div.rpg-collection-card")
    assert cards.count() >= 1, "library must show at least one collection"


def test_collection_page_has_books_table(browser_context):
    page = browser_context
    href = _first_collection_link(page)
    assert href, "expected a collection link"
    page.goto(BASE_URL + href)
    page.wait_for_load_state("networkidle")
    # The books grid must exist and poll the table endpoint with a real ID.
    grid = page.locator("#book-grid")
    assert grid.count() >= 1, "collection page must render the book grid"
    hx_get = grid.get_attribute("hx-get") or ""
    assert re.match(r"^/collections/[a-f0-9]{32}/table$", hx_get), \
        f"table poll URL must have a real collection id, got: {hx_get}"


def test_collection_page_has_ask_form(browser_context):
    page = browser_context
    href = _first_collection_link(page)
    page.goto(BASE_URL + href)
    page.wait_for_load_state("networkidle")
    form = page.locator('#ask-form')
    assert form.count() == 1
    assert form.get_attribute("hx-target") == "body", \
        "ask form must target body (full page swap), not main (doubled header bug)"


# ─── First ask → session (full-page swap) ─────────────────────────────

def test_ask_from_collection_lands_on_chat_page(browser_context):
    """The ask form swaps the whole body — result must be the chat page
    with exactly ONE header bar (regression: doubled header)."""
    page = browser_context
    href = _first_collection_link(page)
    page.goto(BASE_URL + href)
    page.wait_for_load_state("networkidle")

    page.fill('#ask-form input[name="question"]', "What is armor class?")
    page.click('#ask-form button[type="submit"]')
    # Wait for the htmx swap: navigate to the chat page and answer to render.
    page.wait_for_load_state("networkidle")
    time.sleep(8)  # allow the agent loop to complete

    html = page.content()
    # The result must be a chat page (has chat-log), not a doubled page.
    assert page.locator("#chat-log").count() >= 1, "must land on chat page"
    # Exactly one nav header — the doubled-header regression.
    assert html.count('class="rpg-nav"') == 1, "must have exactly one header bar"
    # The ask question must appear as a user turn.
    assert "What is armor class?" in html


def test_ask_followup_renders_turn(browser_context):
    """On the chat page, follow-ups append a turn via htmx (no full reload)."""
    page = browser_context
    page.goto(BASE_URL + "/sessions")
    page.wait_for_load_state("networkidle")
    session_link = page.locator('a[href^="/sessions/"][href*="/sessions/"]')
    n = session_link.count()
    if n == 0:
        pytest.skip("no sessions available")
    page.click(f'a[href^="/sessions/"]')
    page.wait_for_load_state("networkidle")

    turns_before = page.locator("#chat-log .rpg-chat-turn").count()
    page.fill('#chat-form input[name="question"]', "What is hit points?")
    page.click('#chat-form button[type="submit"]')
    # htmx appends the new turn; give the agent loop time.
    time.sleep(10)
    turns_after = page.locator("#chat-log .rpg-chat-turn").count()
    assert turns_after >= turns_before + 1, "follow-up must append a new turn"


# ─── PDF viewer ───────────────────────────────────────────────────────

def test_pdf_citation_link_opens_viewer(browser_context):
    """A citation link must open the PDF viewer (iframe + #page= fragment),
    not just the raw PDF — regression: XFO DENY blocked the iframe."""
    page = browser_context
    # Go to a session and find a citation link.
    page.goto(BASE_URL + "/sessions")
    page.wait_for_load_state("networkidle")
    cite = page.locator("a.rpg-cite-link").first
    if cite.count() == 0:
        pytest.skip("no citation links available")
    # Open in a new tab so we don't navigate away from the session page.
    with page.expect_popup() as popup_info:
        cite.click()
    viewer = popup_info.value
    viewer.wait_for_load_state("networkidle")
    url = viewer.url
    assert "/pdf" in url, f"expected a PDF viewer URL, got: {url}"
    # The viewer must render the iframe wrapper (page jump works via #page=).
    if "?" in url or "#" in url:
        frame = viewer.locator("#pdf-frame")
        assert frame.count() == 1, "PDF viewer must render the iframe wrapper"
    else:
        # No page param → direct PDF file; still must be frameable.
        assert "application/pdf" in (viewer.content() or "")


# ─── Sessions list ────────────────────────────────────────────────────

def test_sessions_list_shows_sessions(browser_context):
    page = browser_context
    page.goto(BASE_URL + "/sessions")
    page.wait_for_load_state("networkidle")
    assert "Recent Sessions" in page.content()
