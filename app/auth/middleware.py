from starlette.middleware.base import BaseHTTPMiddleware
from app.auth.session import verify_session


def current_user_id(request) -> str | None:
    return getattr(request.state, "user_id", None)


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, session_secret: str):
        super().__init__(app)
        self.session_secret = session_secret

    async def dispatch(self, request, call_next):
        token = request.cookies.get("session")
        request.state.user_id = verify_session(token, self.session_secret) if token else None
        return await call_next(request)