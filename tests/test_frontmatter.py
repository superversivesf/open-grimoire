"""Tests for frontmatter YAML round-trip — LLM summaries with quotes must not crash indexing."""

import pytest
from pathlib import Path
from app.pipeline.enrich import Enricher
from app.pipeline.index import parse_frontmatter


def _write(path: Path, summary: str, keywords: list[str] | None = None, page: int | None = None):
    Enricher._write_frontmatter(path, "# Body\n\nText.\n", {"summary": summary, "keywords": keywords or []}, page)


def test_summary_with_quotes_round_trips(tmp_path):
    f = tmp_path / "x.md"
    _write(f, 'Covers the "Attack" action and AC.')
    fm, body = parse_frontmatter(f)
    assert fm["summary"] == 'Covers the "Attack" action and AC.'
    assert "# Body" in body


def test_summary_with_newline_round_trips(tmp_path):
    f = tmp_path / "x.md"
    _write(f, "Line one\nLine two")
    fm, body = parse_frontmatter(f)
    assert fm["summary"] == "Line one\nLine two"


def test_summary_with_colon_and_special_chars(tmp_path):
    f = tmp_path / "x.md"
    _write(f, "Stats: AC 15, HP 7 (goblin)")
    fm, body = parse_frontmatter(f)
    assert fm["summary"] == "Stats: AC 15, HP 7 (goblin)"


def test_keywords_with_commas_and_quotes_round_trip(tmp_path):
    f = tmp_path / "x.md"
    _write(f, "Goblin", ["goblin, monster", 'evil "boss"'])
    fm, body = parse_frontmatter(f)
    assert fm["keywords"] == ["goblin, monster", 'evil "boss"']


def test_parse_frontmatter_handles_corrupt_yaml(tmp_path):
    f = tmp_path / "x.md"
    f.write_text('---\nsummary: "unterminated\nkeywords: [broken\n---\n\n# Body\n')
    fm, body = parse_frontmatter(f)
    assert fm == {}
    assert "# Body" in body


def test_parse_frontmatter_handles_non_str_summary(tmp_path):
    f = tmp_path / "x.md"
    f.write_text("---\nsummary: [a, b]\nkeywords: []\n---\n\n# Body\n")
    fm, body = parse_frontmatter(f)
    assert "# Body" in body
