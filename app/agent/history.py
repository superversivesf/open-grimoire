import json
from app.storage.user_db import get_session


def load_history(conn, session_id: str) -> list[dict]:
    s = get_session(conn, session_id)
    if not s:
        return []
    try:
        return json.loads(s["history_json"])
    except (json.JSONDecodeError, TypeError):
        return []


def append_turn(conn, session_id: str, user_msg: str, agent_msg: str, cites: list[dict] | None = None, suggestions: list[str] | None = None) -> None:
    history = load_history(conn, session_id)
    history.append({"user": user_msg, "agent": agent_msg, "cites": cites or [], "suggestions": suggestions or []})
    conn.execute(
        "UPDATE sessions SET history_json = ?, updated_at = datetime('now') WHERE session_id = ?",
        (json.dumps(history), session_id),
    )
    conn.commit()


def trim_history(history: list[dict], keep_last: int = 6) -> list[dict]:
    if len(history) <= keep_last:
        return history
    return history[-keep_last:]


def build_messages(history: list[dict], system_prompt: str) -> list[dict]:
    msgs = [{"role": "system", "content": system_prompt}]
    for turn in history:
        msgs.append({"role": "user", "content": turn["user"]})
        msgs.append({"role": "assistant", "content": turn["agent"]})
    return msgs