import json
import sqlite3
from typing import Any, cast
from app.storage.user_db import get_session, list_turns, add_turn


def load_history(conn: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    s = get_session(conn, session_id)
    if not s:
        return []
    turns = list_turns(conn, session_id)
    if turns:
        return turns
    # Backfill: sessions created before migration v2 stored history in
    # history_json — migrate once to the turns table.
    try:
        legacy = cast(list[dict[str, Any]], json.loads(s["history_json"]))
    except (json.JSONDecodeError, TypeError):
        return []
    if legacy:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for turn in legacy:
                add_turn(conn, session_id,
                         turn.get("user", ""),
                         turn.get("agent", ""),
                         turn.get("cites", []),
                         turn.get("suggestions", []))
            conn.commit()
        except Exception:
            conn.rollback()
            return legacy
    return list_turns(conn, session_id)


def append_turn(conn: sqlite3.Connection, session_id: str, user_msg: str, agent_msg: str, cites: list[dict[str, Any]] | None = None, suggestions: list[str] | None = None) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        add_turn(conn, session_id, user_msg, agent_msg, cites, suggestions)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def trim_history(history: list[dict[str, Any]], keep_last: int = 6) -> list[dict[str, Any]]:
    """Trim history to keep only the last N turns.
    
    Args:
        history: List of turn dictionaries
        keep_last: Number of recent turns to keep (default: 6)
    
    Returns:
        Trimmed history list
    """
    if len(history) <= keep_last:
        return history
    return history[-keep_last:]


def build_messages(history: list[dict[str, Any]], system_prompt: str) -> list[dict[str, Any]]:
    msgs: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for turn in history:
        msgs.append({"role": "user", "content": turn["user"]})
        msgs.append({"role": "assistant", "content": turn["agent"]})
    return msgs