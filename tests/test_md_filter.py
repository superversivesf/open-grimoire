"""Tests for the `md` template filter — must sanitize untrusted HTML.

The filter renders markdown from two untrusted sources:
- PDF text extracted from user-uploaded documents (doc_leaf.html)
- LLM agent answers (message.html)

Raw HTML in either source must never reach the browser as executable HTML.
"""

import pytest
from app.web.template_utils import create_templates


@pytest.fixture
def md_filter():
    templates = create_templates("app/web/templates")
    return templates.env.filters["md"]


def test_md_strips_script_tags(md_filter):
    out = md_filter('hello <script>alert(1)</script> world')
    assert "<script" not in out
    assert "alert(1)" not in out
    assert "hello" in out


def test_md_strips_event_handlers(md_filter):
    out = md_filter('<img src="x.png" onerror="alert(1)">')
    assert "onerror" not in out


def test_md_strips_javascript_urls(md_filter):
    out = md_filter('[click](javascript:alert(1))')
    assert "javascript:" not in out


def test_md_keeps_safe_markdown(md_filter):
    out = md_filter('**bold** and [link](https://example.com)')
    assert "<strong>bold</strong>" in out
    assert 'href="https://example.com"' in out


def test_md_keeps_images(md_filter):
    out = md_filter('![alt](cover.jpg)')
    assert "<img" in out
    assert 'src="cover.jpg"' in out


def test_md_empty_input(md_filter):
    assert md_filter("") == ""
    assert md_filter(None) == ""
