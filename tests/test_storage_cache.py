"""Tests for _user_storage_used caching — no rglob on the hot path."""

import asyncio
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


@pytest.mark.asyncio
async def test_storage_used_counts_files(tmp_dirs):
    _make_tree(tmp_dirs["data"], "alice", {"a.pdf": 100, "d1/leaf.md": 50})
    assert await _user_storage_used(tmp_dirs["data"], "alice") == 150


@pytest.mark.asyncio
async def test_storage_used_zero_for_missing_user(tmp_dirs):
    assert await _user_storage_used(tmp_dirs["data"], "nobody") == 0


@pytest.mark.asyncio
async def test_storage_used_cached_within_ttl(tmp_dirs):
    _make_tree(tmp_dirs["data"], "alice", {"a.pdf": 100})
    first = await _user_storage_used(tmp_dirs["data"], "alice")
    (tmp_dirs["data"] / "alice" / "b.pdf").write_bytes(b"x" * 200)
    second = await _user_storage_used(tmp_dirs["data"], "alice")
    assert first == second == 100


@pytest.mark.asyncio
async def test_invalidate_storage_forces_recompute(tmp_dirs):
    _make_tree(tmp_dirs["data"], "alice", {"a.pdf": 100})
    await _user_storage_used(tmp_dirs["data"], "alice")
    (tmp_dirs["data"] / "alice" / "b.pdf").write_bytes(b"x" * 200)
    _invalidate_storage("alice")
    assert await _user_storage_used(tmp_dirs["data"], "alice") == 300


@pytest.mark.asyncio
async def test_cache_expires_after_ttl(tmp_dirs, monkeypatch):
    _make_tree(tmp_dirs["data"], "alice", {"a.pdf": 100})
    await _user_storage_used(tmp_dirs["data"], "alice")
    (tmp_dirs["data"] / "alice" / "b.pdf").write_bytes(b"x" * 200)
    monkeypatch.setattr("app.web.routes._STORAGE_TTL", -1)
    assert await _user_storage_used(tmp_dirs["data"], "alice") == 300
