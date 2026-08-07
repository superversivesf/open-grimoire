from fastapi import APIRouter, Request, Form, Response, Depends
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from slowapi import Limiter
from app.auth.passwords import verify_password
from app.auth.session import sign_session
from app.auth.csrf import require_csrf
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
        _limiter = Limiter(key_func=_rate_key)
    return _limiter


def _rate_key(request: Request) -> str:
    """Rate-limit key: first X-Forwarded-For hop when a trusted proxy is
    configured, else the direct client address. X-Forwarded-For is never
    trusted unless TRUST_PROXY_HEADERS=1 (set it only behind your own
    reverse proxy), so clients cannot spoof the key."""
    if os.environ.get("TRUST_PROXY_HEADERS") == "1":
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
        real = request.headers.get("x-real-ip")
        if real:
            return real.strip()
    return request.client.host if request.client else "unknown"


def init_auth_routes(db_dir: Path) -> None:
    global _db_dir
    _db_dir = db_dir


def _is_rate_limited() -> bool:
    """Rate limiting is ON by default; only an explicit RATE_LIMIT_ENABLED=0
    disables it. Mode flags (DEV_MODE/TEST_MODE) must never silently disable
    the limiter — that was a production footgun."""
    return os.environ.get("RATE_LIMIT_ENABLED", "1") != "0"


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
    # Always burn an Argon2 verify — even for unknown users — so login
    # latency does not reveal whether a username exists.
    from app.auth.passwords import _DUMMY_HASH
    hash_to_check = user["password_hash"] if user else _DUMMY_HASH
    valid = verify_password(password, hash_to_check)
    if not user or not valid:
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
async def logout(_: None = Depends(require_csrf)) -> RedirectResponse:
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("session")
    return resp