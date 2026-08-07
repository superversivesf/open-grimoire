"""Admin bootstrap — creates the initial admin user with a secure password.

Never defaults to a known credential. If no password is supplied, a random
one is generated and returned so the caller can print it once.
"""

import secrets
from pathlib import Path

from app.storage.shared_db import init_shared_db, create_user, get_user_by_username
from app.auth.passwords import hash_password


def ensure_admin_user(db_dir: Path, admin_password: str | None = None) -> str | None:
    """Create the admin user if missing.

    Returns the password in effect (provided or generated), or None if the
    admin user already exists.
    """
    conn = init_shared_db(db_dir)
    try:
        if get_user_by_username(conn, "admin"):
            return None
        password = admin_password or secrets.token_urlsafe(18)
        create_user(conn, "admin", hash_password(password), is_admin=True)
        return password
    finally:
        conn.close()
