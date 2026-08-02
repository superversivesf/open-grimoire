from fastapi import APIRouter, Request, Form, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from app.auth.passwords import verify_password
from app.auth.session import sign_session
from app.storage.shared_db import init_shared_db, get_user_by_username

router = APIRouter()
_templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "web" / "templates"))
_db_dir = None


def init_auth_routes(db_dir: Path):
    global _db_dir
    _db_dir = db_dir


@router.get("/login")
async def login_page(request: Request):
    return _templates.TemplateResponse(request, "login.html", {"user_id": None})


@router.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    conn = init_shared_db(_db_dir)
    user = get_user_by_username(conn, username)
    conn.close()
    if not user or not verify_password(password, user["password_hash"]):
        return _templates.TemplateResponse(
            request,
            "login.html",
            {"user_id": None, "error": "Invalid username or password"},
            status_code=401,
        )
    token = sign_session(user["user_id"], request.app.state.session_secret)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("session", token, httponly=True, max_age=86400, samesite="lax")
    return resp


@router.post("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("session")
    return resp