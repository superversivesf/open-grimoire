"""CSRF protection — synchronizer token bound to the session.

The CSRF token is embedded in the signed session payload (set at login)
and must be echoed back on every state-changing request, either as a
hidden form field `_csrf` or the `X-CSRF-Token` header. An attacker's
site cannot read the session cookie (SameSite=Lax + HttpOnly) and cannot
forge the signed payload, so a cross-site POST cannot supply a valid
token.
"""

from fastapi import Request, HTTPException
import hmac
import secrets
from app.auth.session import get_csrf_token

# Test seam, same pattern as the rate limiter's `.enabled` flag: the
# dedicated CSRF tests exercise the real check; other tests disable it.
CSRF_ENABLED = True


async def require_csrf(request: Request) -> None:
    if not CSRF_ENABLED:
        return
    token = request.cookies.get("session")
    if not token:
        raise HTTPException(status_code=403, detail="CSRF check failed")
    expected = get_csrf_token(token, request.app.state.session_secret)
    if not expected:
        raise HTTPException(status_code=403, detail="CSRF check failed")
    supplied = request.headers.get("X-CSRF-Token")
    if supplied is None:
        form = await request.form()
        supplied = form.get("_csrf")
    if not supplied or supplied != expected:
        raise HTTPException(status_code=403, detail="CSRF check failed")


def require_login_csrf(request: Request, supplied: str) -> None:
    """Double-submit check for /login, where no session exists yet.

    GET /login mints a random token, sets it as a cookie, and embeds the
    same value in the form; POST /login must echo it back. The origin
    middleware remains the primary defense; this survives a SameSite=None
    flip or a stripped-Origin client.
    """
    if not CSRF_ENABLED:
        return
    expected = request.cookies.get("login_csrf")
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        raise HTTPException(status_code=403, detail="CSRF check failed")
