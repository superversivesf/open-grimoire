"""Storage limit config tests."""

import pytest
from app.storage.limits import max_upload_bytes, user_storage_limit


def test_defaults(monkeypatch):
    monkeypatch.delenv("MAX_UPLOAD_MB", raising=False)
    monkeypatch.delenv("USER_STORAGE_GB", raising=False)
    assert max_upload_bytes() == 200 * 1024 * 1024
    assert user_storage_limit() == 1024 * 1024 * 1024


def test_env_override(monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "500")
    monkeypatch.setenv("USER_STORAGE_GB", "10")
    assert max_upload_bytes() == 500 * 1024 * 1024
    assert user_storage_limit() == 10 * 1024 * 1024 * 1024


def test_invalid_env_falls_back(monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "garbage")
    monkeypatch.setenv("USER_STORAGE_GB", "nope")
    assert max_upload_bytes() == 200 * 1024 * 1024
    assert user_storage_limit() == 1024 * 1024 * 1024
