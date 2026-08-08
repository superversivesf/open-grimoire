"""resolve_collection — map (collection_id, uid) -> owner + role, or None."""

from pathlib import Path
from typing import Any

from app.storage.shared_db import init_shared_db, get_membership
from app.storage.user_db import init_user_db


def resolve_collection(db_dir: Path, collection_id: str, uid: str) -> dict[str, Any] | None:
    """Return {"collection_id", "owner_uid", "role"} if the user has access.

    Shared collections consult collection_members (owner or member).
    Private collections resolve to their creator (no membership row).
    Returns None for users with no access — callers must redirect/reject.
    """
    conn = init_shared_db(db_dir)
    try:
        membership = get_membership(conn, collection_id, uid)
        if membership:
            # Find the owner row for this collection (role='owner')
            owner = conn.execute(
                "SELECT user_id FROM collection_members WHERE collection_id = ? AND role = 'owner'",
                (collection_id,),
            ).fetchone()
            if owner:
                return {
                    "collection_id": collection_id,
                    "owner_uid": owner["user_id"],
                    "role": membership["role"],
                }
    finally:
        conn.close()
    # Private fallback: the collection exists in the user's own DB
    uconn = init_user_db(db_dir, uid)
    try:
        row = uconn.execute(
            "SELECT name FROM collections WHERE collection_id = ?", (collection_id,)
        ).fetchone()
        if row:
            return {"collection_id": collection_id, "owner_uid": uid, "role": "owner"}
    finally:
        uconn.close()
    return None
