"""Tests for token estimation in app/usage/tokens.py."""
import pytest
from app.usage.tokens import (
    estimate_tokens,
    estimate_messages_tokens,
    estimate_response_tokens,
)


def test_estimate_tokens_empty_is_zero():
    assert estimate_tokens("") == 0
    assert estimate_tokens(None) == 0


def test_estimate_tokens_short_text_is_at_least_one():
    # len("ac") // 4 == 0, but a non-empty string must never estimate 0 tokens.
    assert estimate_tokens("ac") == 1
    assert estimate_tokens("a") == 1


@pytest.mark.parametrize("length", [16, 100, 1000, 4096])
def test_estimate_tokens_long_text_scales_with_length(length):
    text = "the quick brown fox jumps over the lazy dog " * (length // 10)
    assert estimate_tokens(text) == max(1, len(text) // 4)


def test_estimate_tokens_unicode_text():
    # Unicode chars still count toward the char-based estimate.
    text = "héllo wörld — 日本語テキスト · émojis 🐉"
    expected = max(1, len(text) // 4)
    assert estimate_tokens(text) == expected
    assert estimate_tokens(text) > 0


def test_estimate_messages_tokens_counts_content_and_overhead():
    messages = [
        {"role": "user", "content": "What is a goblin's AC?"},
        {"role": "assistant", "content": "AC 15."},
    ]
    total = estimate_messages_tokens(messages)
    # 2 messages * 4 overhead + content estimates
    assert total >= estimate_tokens("What is a goblin's AC?") + estimate_tokens("AC 15.") + 8


def test_estimate_messages_tokens_handles_missing_content():
    assert estimate_messages_tokens([{"role": "user"}]) == 4
    assert estimate_messages_tokens([]) == 0


def test_estimate_response_tokens_delegates_to_estimate_tokens():
    assert estimate_response_tokens("hello world foo") == estimate_tokens("hello world foo")
