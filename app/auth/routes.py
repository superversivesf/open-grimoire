from fastapi import APIRouter, Request, Form, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from slowapi import Limiter
from slowapi.util import get_remote_address
from app.auth.passwords import verify_password
from app.auth.session import sign_session
from app.storage.shared_db import init_shared_db, get_user_by_username
from app.web.template_utils import create_templates
from app.config import Config
from app.constants import SESSION_COOKIE_MAX_AGE, LOGIN_RATE_LIMIT
import os

router = APIRouter()
_templates = create_templates(str(Path(__file__).parent.parent / "web" / "templates"))
# Late-init module config: set by init_auth_routes() from create_app() before
# any request is served. Typed as Path (not Optional) to reflect that invariant.
_db_dir: Path = Path()
_limiter: Limiter | None = None


def _get_limiter() -> Limiter:
    global _limiter
    if _limiter is None:
        _limiter = Limiter(key_func=get_remote_address)
    return _limiter


def init_auth_routes(db_dir: Path) -> None:
    global _db_dir
    _db_dir = db_dir


def _is_rate_limited() -> bool:
    """Check if rate limiting should be applied."""
    return not (os.environ.get("DEV_MODE") or os.environ.get("TEST_MODE"))


@router.get("/login")
async def login_page(request: Request) -> Response:
    return _templates.TemplateResponse(request, "login.html", {"user_id": None})


@router.post("/login")
@_get_limiter().limit(LOGIN_RATE_LIMIT)
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)) -> Response:
    # Rate limiting is enforced by the @limiter.limit decorator above, gated by
    # `limiter.enabled` (set in create_app from _is_rate_limited()). When the
    # limit is exceeded slowapi raises RateLimitExceeded → handled as 429.
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
    token = sign_session(user["user_id"], request.app.state.session_secret, is_admin=user.get("is_admin", False))
    resp = RedirectResponse("/", status_code=303)
    secure = getattr(request.app.state.config, "cookie_secure", False)
    resp.set_cookie("session", token, httponly=True, max_age=SESSION_COOKIE_MAX_AGE, samesite="lax", secure=secure)
    return resp


@router.post("/logout")
async def logout() -> RedirectResponse:
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("session")
    return resp