import pytest
from click.testing import CliRunner
from app.cli.user import create_cmd
from app.storage.shared_db import init_shared_db, get_user_by_username
from app.auth.passwords import verify_password


def test_cli_create_user(tmp_dirs, monkeypatch):
    monkeypatch.setattr("app.cli.user._db_dir", tmp_dirs["db"])
    runner = CliRunner()
    result = runner.invoke(create_cmd, ["--username", "alice", "--password", "pw123"])
    assert result.exit_code == 0
    conn = init_shared_db(tmp_dirs["db"])
    u = get_user_by_username(conn, "alice")
    assert u is not None
    assert verify_password("pw123", u["password_hash"])
    assert u["is_admin"] == 0
    conn.close()


def test_cli_create_admin(tmp_dirs, monkeypatch):
    monkeypatch.setattr("app.cli.user._db_dir", tmp_dirs["db"])
    runner = CliRunner()
    result = runner.invoke(create_cmd, ["--username", "admin", "--password", "x", "--admin"])
    assert result.exit_code == 0
    conn = init_shared_db(tmp_dirs["db"])
    u = get_user_by_username(conn, "admin")
    assert u["is_admin"] == 1
    conn.close()