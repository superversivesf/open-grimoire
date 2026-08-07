import base64
import hmac
import hashlib
import json
import secrets
import time
from app.constants import SESSION_TTL_SECONDS


def sign_session(user_id: str, secret: str, ttl_seconds: int = SESSION_TTL_SECONDS, is_admin: bool = False) -> str:
    payload = {
        "user_id": user_id,
        "is_admin": is_admin,
        "csrf": secrets.token_urlsafe(16),
        "exp": int(time.time()) + ttl_seconds,
    }
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    sig = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def verify_session(token: str, secret: str) -> tuple[str | None, bool]:
    """Returns (user_id, is_admin)"""
    try:
        raw, sig = token.rsplit(".", 1)
    except ValueError:
        return None, False
    expected = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None, False
    try:
        payload = json.loads(base64.urlsafe_b64decode(raw))
    except Exception:
        return None, False
    if int(payload.get("exp", 0)) < int(time.time()):
        return None, False
    return payload.get("user_id"), payload.get("is_admin", False)


def get_csrf_token(token: str, secret: str) -> str | None:
    """Extract the CSRF token from a valid session token, or None."""
    try:
        raw, sig = token.rsplit(".", 1)
    except ValueError:
        return None
    expected = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(raw))
    except Exception:
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    csrf = payload.get("csrf")
    return csrf if isinstance(csrf, str) and csrf else None