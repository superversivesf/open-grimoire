"""Tests for content hashing / watermark stripping (app/pipeline/content_hash.py).

The purpose of content_hash is book deduplication: two users uploading the
same book must hash identically even when their copies carry different
DriveThruRPG watermarks, while different books must hash differently.
"""
import pytest
from app.pipeline.content_hash import content_hash, strip_watermarks


def test_identical_content_same_hash():
    text = "Goblins have AC 15 and HP 7.\n\nThey are small and cunning.\n"
    assert content_hash(text) == content_hash(text)


def test_watermarked_variants_same_hash():
    """Email, buyer-line, order-id and hex watermarks must not change the hash."""
    base = "Goblins have AC 15 and HP 7. They are small and cunning."
    variants = [
        base,
        f"{base}\nPurchased by John Doe",
        f"{base}\nPrepared for Jane Smith",
        f"{base}\nPrepared for: Jane Smith\nOrder #84932",
        f"{base}\nDownloaded by jdoe@example.com on 2026-01-15",
        f"{base}\nTransaction: 3F9A2C1D4E5B6071",
        f"{base}\nWatermark: 9A8B7C6D5E4F3210",
        f"{base}\nhttps://www.drivethrurpg.com/product/12345",
        f"{base}\nhttps://www.dtrpg.com/browse.php",
    ]
    hashes = {content_hash(v) for v in variants}
    assert len(hashes) == 1, "watermarked variants must share one hash"


def test_watermark_removal_does_not_affect_real_content():
    """Page content that merely contains a date or a 8-char hex token is
    deliberately normalized away — confirm the surviving text is the core."""
    text = "The dragon breathes fire.\nPrepared for: Alice\n2026-02-03"
    stripped = strip_watermarks(text)
    assert "dragon breathes fire" in stripped
    assert "Alice" not in stripped


def test_different_content_different_hash():
    a = content_hash("Goblins have AC 15 and HP 7.")
    b = content_hash("Goblins have AC 12 and HP 9.")
    c = content_hash("Orcs have AC 13 and HP 15.")
    assert a != b
    assert a != c
    assert b != c


def test_hash_is_whitespace_and_case_insensitive():
    """Normalization folds case + whitespace so re-extractions agree."""
    h1 = content_hash("Goblin  AC\t15.\n\nHP 7.")
    h2 = content_hash("goblin ac 15. hp 7.")
    assert h1 == h2


def test_hash_is_deterministic_hex():
    h = content_hash("A stable string.")
    assert len(h) == 64
    int(h, 16)  # raises if not hex


def test_strip_watermarks_normalizes_whitespace():
    text = "Line one.\n\n\n\n   Line two.\t\tEnd."
    stripped = strip_watermarks(text)
    assert "\n\n\n" not in stripped
    assert stripped == stripped.strip()
