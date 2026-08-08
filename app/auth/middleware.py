from pathlib import Path
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp
from app.auth.session import verify_session, get_csrf_token


# Paths that don't require authentication
SKIP_AUTH_PATHS = {"/healthz", "/readyz", "/static", "/login", "/register", "/favicon.ico"}


def current_user_id(request: Request) -> str | None:
    return getattr(request.state, "user_id", None)


def is_admin(request: Request) -> bool:
    return getattr(request.state, "is_admin", False)


def _should_skip_auth(path: str) -> bool:
    """Check if path should skip authentication."""
    if path in SKIP_AUTH_PATHS:
        return True
    if path.startswith("/static/"):
        return True
    return False


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, session_secret: str, db_dir: Path | None = None) -> None:
        super().__init__(app)
        self.session_secret = session_secret
        self.db_dir = db_dir

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Skip auth for health checks and static assets
        if _should_skip_auth(request.url.path):
            return await call_next(request)

        token = request.cookies.get("session")
        uid, is_admin = verify_session(token, self.session_secret) if token else (None, False)
        request.state.user_id = uid
        request.state.is_admin = is_admin
        request.state.csrf_token = get_csrf_token(token, self.session_secret) if token else None

        # Fallback: if is_admin not in token (old sessions), check DB once
        if uid and not is_admin and self.db_dir:
            try:
                from app.storage.shared_db import init_shared_db
                sconn = init_shared_db(self.db_dir)
                row = sconn.execute(
                    "SELECT is_admin FROM users WHERE user_id = ?", (uid,)
                ).fetchone()
                if row and row["is_admin"]:
                    request.state.is_admin = True
                sconn.close()
            except Exception:
                pass

        return await call_next(request)