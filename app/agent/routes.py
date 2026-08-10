from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import RedirectResponse, StreamingResponse, Response
from fastapi.templating import Jinja2Templates
from pathlib import Path
from typing import Any, AsyncGenerator, cast
import time
import json
from app.auth.middleware import current_user_id
from app.auth.csrf import require_csrf
from app.storage.user_db import (
    init_user_db, create_session, get_session, list_collections, list_docs,
)
from app.storage.shared_db import init_shared_db, log_query
from app.agent.history import load_history, append_turn
from app.agent.tools import ToolBox
from app.agent.loop import AgentLoop
from app.web.template_utils import create_templates

router = APIRouter()
_templates = create_templates(str(Path(__file__).parent.parent / "web" / "templates"))
# Late-init module config: kept as no-ops for backward compat with tests.
# Routes now read from request.app.state.* instead of module globals.
_db_dir: Path = Path()
_data_dir: Path = Path()
_gateway = None


def init_agent_routes(db_dir: Path, data_dir: Path, gateway: Any) -> None:
    """No-op for backward compatibility. Routes read from request.app.state."""
    pass


def _make_loop(request: Request, uid: str, collection_id: str, owner_uid: str | None = None) -> AgentLoop:
    toolbox = ToolBox(request.app.state.data_dir, uid, request.app.state.db_dir, collection_id, owner_uid=owner_uid)
    factory = getattr(request.app.state, "agent_loop_factory", None) or getattr(request.app.state.gateway, "agent_loop_factory", None)
    if factory:
        return cast(AgentLoop, factory(toolbox))
    return AgentLoop(request.app.state.gateway, toolbox)


def _resolve_owner(request: Request, collection_id: str) -> tuple[str | None, bool]:
    """(owner_uid, is_authenticated) via the sharing seam; (None, False) = login."""
    uid = current_user_id(request)
    if not uid:
        return None, False
    from app.storage.resolver import resolve_collection
    r = resolve_collection(request.app.state.db_dir, collection_id, uid)
    if not r:
        return None, True
    return r["owner_uid"], True


def _model_for(gateway: Any, role: str) -> str:
    """Resolve the model name for a role, tolerating mocks/fakes without .models."""
    models = getattr(gateway, "models", None)
    if isinstance(models, dict):
        return str(models.get(role, "unknown"))
    return "unknown"


@router.post("/sessions")
async def start_session(request: Request, collection_id: str = Form(...), question: str = Form(...), _: None = Depends(require_csrf)) -> Response:
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    owner, auth = _resolve_owner(request, collection_id)
    if not owner:
        return RedirectResponse("/login" if not auth else "/", status_code=303)
    conn = init_user_db(request.app.state.db_dir, uid)
    try:
        sid = create_session(conn, collection_id, name=question[:80])
        history = load_history(conn, sid)
    finally:
        conn.close()
    loop = _make_loop(request, uid, collection_id, owner_uid=owner)
    t0 = time.time()
    result = await loop.run(history, question)
    elapsed = time.time() - t0
    model = _model_for(request.app.state.gateway, "query")
    sconn = init_shared_db(request.app.state.db_dir)
    log_query(sconn, uid, model, question, result["answer"],
              iterations=result.get("iterations", 0),
              citations=len(result.get("cites", [])),
              est_input_tokens=result.get("est_input_tokens", 0),
              est_output_tokens=result.get("est_output_tokens", 0),
              elapsed_sec=elapsed,
              done_called=result.get("done_called", False),
              session_id=sid, collection_id=collection_id)
    sconn.close()
    conn = init_user_db(request.app.state.db_dir, uid)
    try:
        append_turn(conn, sid, question, result["answer"], result["cites"], result.get("suggestions"))
        session = get_session(conn, sid)
        return _templates.TemplateResponse(
            request,
            "chat.html",
            {"user_id": uid, "session": session, "history": load_history(conn, sid)},
        )
    finally:
        conn.close()


@router.post("/sessions/{session_id}")
async def continue_session(request: Request, session_id: str, question: str = Form(...), _: None = Depends(require_csrf)) -> Response:
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    conn = init_user_db(request.app.state.db_dir, uid)
    try:
        session = get_session(conn, session_id)
        if not session:
            return RedirectResponse("/sessions", status_code=303)
        history = load_history(conn, session_id)
    finally:
        conn.close()
    owner, auth = _resolve_owner(request, session["collection_id"])
    if not owner:
        return RedirectResponse("/login" if not auth else "/", status_code=303)
    loop = _make_loop(request, uid, session["collection_id"], owner_uid=owner)
    t0 = time.time()
    result = await loop.run(history, question)
    elapsed = time.time() - t0
    model = _model_for(request.app.state.gateway, "query")
    sconn = init_shared_db(request.app.state.db_dir)
    log_query(sconn, uid, model, question, result["answer"],
              iterations=result.get("iterations", 0),
              citations=len(result.get("cites", [])),
              est_input_tokens=result.get("est_input_tokens", 0),
              est_output_tokens=result.get("est_output_tokens", 0),
              elapsed_sec=elapsed,
              done_called=result.get("done_called", False),
              session_id=session_id, collection_id=session["collection_id"])
    sconn.close()
    conn = init_user_db(request.app.state.db_dir, uid)
    try:
        append_turn(conn, session_id, question, result["answer"], result["cites"], result.get("suggestions"))
        new_turn = {"user": question, "agent": result["answer"], "cites": result["cites"], "suggestions": result.get("suggestions", [])}
        return _templates.TemplateResponse(
            request,
            "_message.html",
            {"user_id": uid, "turn": new_turn, "collection_id": session["collection_id"]},
        )
    finally:
        conn.close()


def _sse_format(event_type: str, data: dict[str, Any]) -> str:
    return f"data: {json.dumps({'type': event_type, **data})}\n\n"


@router.post("/sessions/{session_id}/stream")
async def continue_session_stream(request: Request, session_id: str, question: str = Form(...), _: None = Depends(require_csrf)) -> Response:
    """SSE streaming endpoint for follow-up questions."""
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)

    async def event_stream() -> AsyncGenerator[str, None]:
        # Snapshot session + history up-front, then close the connection —
        # it must not sit open (holding a write lock) for the whole LLM run.
        conn = init_user_db(request.app.state.db_dir, uid)
        try:
            session = get_session(conn, session_id)
            if not session:
                yield _sse_format("error", {"message": "Session not found"})
                return
            history = load_history(conn, session_id)
        finally:
            conn.close()
        owner, auth = _resolve_owner(request, session["collection_id"])
        if not owner:
            yield _sse_format("error", {"message": "Access denied"})
            return
        loop = _make_loop(request, uid, session["collection_id"], owner_uid=owner)

        async for event in loop.run_stream(history, question):
            if event["type"] == "thinking":
                yield _sse_format("thinking", {"message": event["message"]})
            elif event["type"] == "done":
                conn = init_user_db(request.app.state.db_dir, uid)
                try:
                    append_turn(conn, session_id, question, event["answer"], event.get("cites", []), event.get("suggestions", []))
                finally:
                    conn.close()
                model = _model_for(request.app.state.gateway, "query")
                sconn = init_shared_db(request.app.state.db_dir)
                log_query(sconn, uid, model, question, event["answer"],
                          iterations=event.get("iterations", 0),
                          citations=len(event.get("cites", [])),
                          est_input_tokens=event.get("est_input_tokens", 0),
                          est_output_tokens=event.get("est_output_tokens", 0),
                          elapsed_sec=0,
                          done_called=event.get("done_called", False),
                          session_id=session_id, collection_id=session["collection_id"])
                sconn.close()
                yield _sse_format("done", {
                    "answer": event["answer"],
                    "cites": event.get("cites", []),
                    "suggestions": event.get("suggestions", []),
                    "iterations": event.get("iterations", 0),
                })
            elif event["type"] == "error":
                yield _sse_format("error", {"message": event.get("message", "Unknown error")})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/sessions")
async def list_sessions(request: Request) -> Response:
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    conn = init_user_db(request.app.state.db_dir, uid)
    rows = conn.execute(
        "SELECT session_id, collection_id, name, created_at, updated_at FROM sessions ORDER BY updated_at DESC LIMIT 20"
    ).fetchall()
    sessions = [dict(r) for r in rows]
    conn.close()
    return _templates.TemplateResponse(
        request,
        "sessions.html",
        {"user_id": uid, "sessions": sessions},
    )


@router.get("/sessions/{session_id}")
async def view_session(request: Request, session_id: str) -> Response:
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    conn = init_user_db(request.app.state.db_dir, uid)
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


@router.delete("/sessions/{session_id}")
async def delete_session(request: Request, session_id: str, _: None = Depends(require_csrf)) -> Response:
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    conn = init_user_db(request.app.state.db_dir, uid)
    conn.execute("DELETE FROM turns WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
    conn.commit()
    conn.close()
    return RedirectResponse("/sessions", status_code=303)