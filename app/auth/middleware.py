from starlette.middleware.base import BaseHTTPMiddleware
from app.auth.session import verify_session


def current_user_id(request) -> str | None:
    return getattr(request.state, "user_id", None)


def is_admin(request) -> bool:
    return getattr(request.state, "is_admin", False)


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, session_secret: str, db_dir=None):
        super().__init__(app)
        self.session_secret = session_secret
        self.db_dir = db_dir

    async def dispatch(self, request, call_next):
        token = request.cookies.get("session")
        uid = verify_session(token, self.session_secret) if token else None
        request.state.user_id = uid

        # Check admin status — only for authenticated users
        request.state.is_admin = False
        if uid and self.db_dir:
            try:
                from app.storage.shared_db import init_shared_db, list_users
                sconn = init_shared_db(self.db_dir)
                row = sconn.execute(
                    "SELECT is_admin FROM users WHERE user_id = ?", (uid,)
                ).fetchone()
                request.state.is_admin = bool(row and row["is_admin"])
                sconn.close()
            except Exception:
                pass

        return await call_next(request)