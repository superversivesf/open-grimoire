from pathlib import Path
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp
from app.auth.session import verify_session, get_csrf_token
from app.logging_utils import get_logger


log = get_logger("auth")

SKIP_AUTH_PATHS = {"/healthz", "/readyz", "/static", "/login", "/register", "/favicon.ico"}


def current_user_id(request: Request) -> str | None:
    return getattr(request.state, "user_id", None)


def is_admin(request: Request) -> bool:
    return getattr(request.state, "is_admin", False)


def _should_skip_auth(path: str) -> bool:
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
        if _should_skip_auth(request.url.path):
            return await call_next(request)

        token = request.cookies.get("session")
        uid, is_admin = verify_session(token, self.session_secret) if token else (None, False)
        request.state.user_id = uid
        request.state.is_admin = is_admin
        request.state.csrf_token = get_csrf_token(token, self.session_secret) if token else None

        if uid and self.db_dir:
            # Re-verify the user against the DB on every request: the signed
            # token's is_admin flag and the account's status can change
            # (demotion, rejection) while the cookie is still valid.
            try:
                from app.storage.shared_db import init_shared_db
                sconn = init_shared_db(self.db_dir)
                try:
                    row = sconn.execute(
                        "SELECT is_admin, status FROM users WHERE user_id = ?", (uid,)
                    ).fetchone()
                finally:
                    sconn.close()
                if row is None:
                    request.state.user_id = None
                    request.state.is_admin = False
                else:
                    request.state.is_admin = bool(row["is_admin"])
                    status = row["status"]
                    if status not in ("active", None):
                        request.state.user_id = None
                        request.state.is_admin = False
            except Exception:
                # DB hiccup must not take the whole app down; the token
                # payload remains the fallback for this request.
                log.exception("auth DB verification failed; falling back to token payload")

        return await call_next(request)