import json
from app.agent.history import load_history, append_turn, trim_history, build_messages
from app.storage.user_db import init_user_db, create_collection, create_session


def test_load_history_empty(tmp_dirs):
    conn = init_user_db(tmp_dirs["db"], "alice")
    cid = create_collection(conn, "C")
    sid = create_session(conn, cid)
    h = load_history(conn, sid)
    assert h == []
    conn.close()


def test_append_and_load(tmp_dirs):
    conn = init_user_db(tmp_dirs["db"], "alice")
    cid = create_collection(conn, "C")
    sid = create_session(conn, cid)
    append_turn(conn, sid, "What is AC?", "AC is armor class.", [{"path": "x.md", "page": 5, "quote": "AC 15"}])
    h = load_history(conn, sid)
    assert len(h) == 1
    assert h[0]["user"] == "What is AC?"
    assert h[0]["agent"] == "AC is armor class."
    assert h[0]["cites"][0]["page"] == 5
    conn.close()


def test_trim_history_keeps_last_n():
    history = [{"user": f"q{i}", "agent": f"a{i}", "cites": []} for i in range(10)]
    trimmed = trim_history(history, keep_last=3)
    assert len(trimmed) == 3
    assert trimmed[0]["user"] == "q7"
    assert trimmed[2]["user"] == "q9"


def test_build_messages_format():
    history = [{"user": "hi", "agent": "hello", "cites": []}]
    msgs = build_messages(history, "You are a helpful RPG rules assistant.")
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == "You are a helpful RPG rules assistant."
    assert msgs[1]["role"] == "user"
    assert msgs[1]["content"] == "hi"
    assert msgs[2]["role"] == "assistant"
    assert msgs[2]["content"] == "hello"