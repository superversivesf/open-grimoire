import pytest
from pathlib import Path
from app.agent.sandbox import safe_read_file, truncate_result, safe_ls


def test_safe_read_file(tmp_dirs):
    f = tmp_dirs["data"] / "alice" / "d1" / "x.md"
    f.parent.mkdir(parents=True)
    f.write_text("# Hello\n\nContent here.\n")
    result = safe_read_file(tmp_dirs["data"], "alice", str(f))
    assert "Hello" in result
    assert "Content here" in result


def test_safe_read_file_rejects_escape(tmp_dirs):
    result = safe_read_file(tmp_dirs["data"], "alice", "/etc/passwd")
    assert "invalid path" in result


def test_safe_read_file_line_range(tmp_dirs):
    f = tmp_dirs["data"] / "alice" / "d1" / "x.md"
    f.parent.mkdir(parents=True)
    f.write_text("\n".join(f"line {i}" for i in range(100)))
    result = safe_read_file(tmp_dirs["data"], "alice", str(f), lines="10-15")
    assert "line 10" in result
    assert "line 15" in result
    assert "line 16" not in result
    assert "line 9" not in result


def test_truncate_result_short_text():
    assert truncate_result("short", 1000) == "short"


def test_truncate_result_long_text():
    text = "x" * 20000
    result = truncate_result(text, max_chars=1000)
    assert len(result) < 1100
    assert "truncated" in result.lower() or "file is long" in result.lower()


def test_safe_ls(tmp_dirs):
    d = tmp_dirs["data"] / "alice" / "d1"
    d.mkdir(parents=True)
    (d / "a.md").write_text("a")
    (d / "b.md").write_text("b")
    result = safe_ls(tmp_dirs["data"], "alice", str(d))
    assert "a.md" in result
    assert "b.md" in result


def test_safe_ls_rejects_escape(tmp_dirs):
    result = safe_ls(tmp_dirs["data"], "alice", "/etc")
    assert result == []