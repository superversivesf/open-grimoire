import base64
import hashlib
import hmac
import json
import secrets
import time

from cryptography.fernet import Fernet

from app.constants import SESSION_TTL_SECONDS


def _derive_fernet_key(secret: str) -> bytes:
    """Derive a 32-byte Fernet key from the session secret via SHA-256."""
    return base64.urlsafe_b64encode(
        hashlib.sha256(secret.encode()).digest()
    )


def _encrypt_payload(payload: dict, secret: str) -> bytes:
    """Encrypt the session payload with Fernet."""
    key = _derive_fernet_key(secret)
    return Fernet(key).encrypt(json.dumps(payload).encode())


def _decrypt_payload(encrypted: bytes, secret: str) -> dict | None:
    """Decrypt a Fernet-encrypted session payload, or None on failure."""
    key = _derive_fernet_key(secret)
    try:
        plain = Fernet(key).decrypt(encrypted)
        return json.loads(plain)
    except Exception:
        return None


def _decrypt_session_token(token: str, secret: str) -> dict | None:
    """Decrypt and validate a session token, returning the payload once.
    
    This helper avoids double Fernet decryption by decrypting once and
    returning the full payload for both verify_session and get_csrf_token.
    
    Returns:
        The decrypted payload dict if valid, None otherwise.
    """
    try:
        raw, sig = token.rsplit(".", 1)
    except ValueError:
        return None
    expected = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        encrypted = base64.urlsafe_b64decode(raw)
    except Exception:
        return None
    payload = _decrypt_payload(encrypted, secret)
    if payload is None:
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload


def sign_session(user_id: str, secret: str, ttl_seconds: int = SESSION_TTL_SECONDS, is_admin: bool = False) -> str:
    payload = {
        "user_id": user_id,
        "is_admin": is_admin,
        "csrf": secrets.token_urlsafe(16),
        "exp": int(time.time()) + ttl_seconds,
    }
    encrypted = _encrypt_payload(payload, secret)
    raw = base64.urlsafe_b64encode(encrypted).decode()
    sig = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def verify_session(token: str, secret: str) -> tuple[str | None, bool]:
    """Returns (user_id, is_admin)"""
    payload = _decrypt_session_token(token, secret)
    if payload is None:
        return None, False
    return payload.get("user_id"), payload.get("is_admin", False)


def get_csrf_token(token: str, secret: str) -> str | None:
    """Extract the CSRF token from a valid session token, or None."""
    payload = _decrypt_session_token(token, secret)
    if payload is None:
        return None
    csrf = payload.get("csrf")
    return csrf if isinstance(csrf, str) and csrf else None