from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import json
from app.auth.middleware import current_user_id
from app.storage.user_db import (
    init_user_db, create_session, get_session, list_collections, list_docs,
)
from app.agent.history import load_history, append_turn
from app.agent.tools import ToolBox
from app.agent.loop import AgentLoop

router = APIRouter()
_templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "web" / "templates"))
_db_dir = None
_data_dir = None
_gateway = None


def init_agent_routes(db_dir: Path, data_dir: Path, gateway):
    global _db_dir, _data_dir, _gateway
    _db_dir = db_dir
    _data_dir = data_dir
    _gateway = gateway


def _make_loop(request: Request, uid: str, collection_id: str):
    toolbox = ToolBox(_data_dir, uid, _db_dir, collection_id)
    factory = getattr(request.app.state, "agent_loop_factory", None) or getattr(_gateway, "agent_loop_factory", None)
    if factory:
        return factory(toolbox)
    return AgentLoop(_gateway, toolbox)


@router.post("/sessions")
async def start_session(request: Request, collection_id: str = Form(...), question: str = Form(...)):
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    conn = init_user_db(_db_dir, uid)
    try:
        sid = create_session(conn, collection_id)
        history = load_history(conn, sid)
        loop = _make_loop(request, uid, collection_id)
        result = await loop.run(history, question)
        append_turn(conn, sid, question, result["answer"], result["cites"])
        session = get_session(conn, sid)
        return _templates.TemplateResponse(
            request,
            "chat.html",
            {"user_id": uid, "session": session, "history": load_history(conn, sid)},
        )
    finally:
        conn.close()


@router.post("/sessions/{session_id}")
async def continue_session(request: Request, session_id: str, question: str = Form(...)):
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    conn = init_user_db(_db_dir, uid)
    try:
        session = get_session(conn, session_id)
        if not session:
            return RedirectResponse("/sessions", status_code=303)
        history = load_history(conn, session_id)
        loop = _make_loop(request, uid, session["collection_id"])
        result = await loop.run(history, question)
        append_turn(conn, session_id, question, result["answer"], result["cites"])
        new_turn = {"user": question, "agent": result["answer"], "cites": result["cites"]}
        return _templates.TemplateResponse(
            request,
            "_message.html",
            {"user_id": uid, "turn": new_turn},
        )
    finally:
        conn.close()


@router.get("/sessions")
async def list_sessions(request: Request):
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    conn = init_user_db(_db_dir, uid)
    rows = conn.execute(
        "SELECT session_id, collection_id, created_at, updated_at FROM sessions ORDER BY updated_at DESC LIMIT 20"
    ).fetchall()
    sessions = [dict(r) for r in rows]
    conn.close()
    return _templates.TemplateResponse(
        request,
        "sessions.html",
        {"user_id": uid, "sessions": sessions},
    )


@router.get("/sessions/{session_id}")
async def view_session(request: Request, session_id: str):
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    conn = init_user_db(_db_dir, uid)
    session = get_session(conn, session_id)
    if not session:
        conn.close()
        return RedirectResponse("/sessions", status_code=303)
    history = load_history(conn, session_id)
    conn.close()
    return _templates.TemplateResponse(
        request,
        "chat.html",
        {"user_id": uid, "session": session, "history": history},
    )