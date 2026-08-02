import pytest
from pathlib import Path
from app.storage.paths import user_data_dir, user_db_path, validate_user_path


def test_user_data_dir_creates_and_returns(tmp_dirs):
    d = user_data_dir(tmp_dirs["data"], "alice")
    assert d == tmp_dirs["data"] / "alice"
    assert d.is_dir()


def test_user_db_path_returns_path(tmp_dirs):
    p = user_db_path(tmp_dirs["db"], "alice")
    assert p == tmp_dirs["db"] / "alice.sqlite"


def test_validate_user_path_accepts_inside(tmp_dirs):
    target = str(tmp_dirs["data"] / "alice" / "doc1" / "index.md")
    result = validate_user_path(tmp_dirs["data"], "alice", target)
    assert result == (tmp_dirs["data"] / "alice" / "doc1" / "index.md").resolve()


def test_validate_user_path_rejects_dotdot(tmp_dirs):
    target = str(tmp_dirs["data"] / "alice" / ".." / "bob" / "secret.md")
    with pytest.raises(ValueError, match="outside"):
        validate_user_path(tmp_dirs["data"], "alice", target)


def test_validate_user_path_rejects_absolute(tmp_dirs):
    with pytest.raises(ValueError, match="outside"):
        validate_user_path(tmp_dirs["data"], "alice", "/etc/passwd")


def test_validate_user_path_rejects_symlink_escape(tmp_dirs):
    alice_dir = tmp_dirs["data"] / "alice"
    alice_dir.mkdir()
    outside = tmp_dirs["data"] / "outside.txt"
    outside.write_text("nope")
    link = alice_dir / "link"
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="outside"):
        validate_user_path(tmp_dirs["data"], "alice", str(link))