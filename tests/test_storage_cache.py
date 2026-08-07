"""Tests for _user_storage_used caching — no rglob on the hot path."""

import time
import pytest
from pathlib import Path
from app.web.routes import _user_storage_used, _invalidate_storage, _STORAGE_TTL


@pytest.fixture(autouse=True)
def clear_cache():
    _invalidate_storage("__all__")
    yield
    _invalidate_storage("__all__")


def _make_tree(data_dir: Path, uid: str, files: dict[str, int]):
    user_dir = data_dir / uid
    for name, size in files.items():
        p = user_dir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)


def test_storage_used_counts_files(tmp_dirs):
    _make_tree(tmp_dirs["data"], "alice", {"a.pdf": 100, "d1/leaf.md": 50})
    assert _user_storage_used(tmp_dirs["data"], "alice") == 150


def test_storage_used_zero_for_missing_user(tmp_dirs):
    assert _user_storage_used(tmp_dirs["data"], "nobody") == 0


def test_storage_used_cached_within_ttl(tmp_dirs):
    _make_tree(tmp_dirs["data"], "alice", {"a.pdf": 100})
    first = _user_storage_used(tmp_dirs["data"], "alice")
    # Add a file without invalidating — cache must still return the old value
    (tmp_dirs["data"] / "alice" / "b.pdf").write_bytes(b"x" * 200)
    second = _user_storage_used(tmp_dirs["data"], "alice")
    assert first == second == 100


def test_invalidate_storage_forces_recompute(tmp_dirs):
    _make_tree(tmp_dirs["data"], "alice", {"a.pdf": 100})
    _user_storage_used(tmp_dirs["data"], "alice")
    (tmp_dirs["data"] / "alice" / "b.pdf").write_bytes(b"x" * 200)
    _invalidate_storage("alice")
    assert _user_storage_used(tmp_dirs["data"], "alice") == 300


def test_cache_expires_after_ttl(tmp_dirs, monkeypatch):
    _make_tree(tmp_dirs["data"], "alice", {"a.pdf": 100})
    _user_storage_used(tmp_dirs["data"], "alice")
    (tmp_dirs["data"] / "alice" / "b.pdf").write_bytes(b"x" * 200)
    monkeypatch.setattr("app.web.routes._STORAGE_TTL", -1)
    assert _user_storage_used(tmp_dirs["data"], "alice") == 300
