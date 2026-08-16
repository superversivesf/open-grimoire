"""Orphaned-doc cleanup queue.

When a collection delete removes DB rows but filesystem cleanup fails
(rmtree error, crash between DB delete and FS delete), the orphaned doc
dirs are recorded here so a startup sweep can retry them.
"""
import json
from pathlib import Path
from typing import Any

_ORPHANS_FILE = "orphans.json"


def _orphans_path(data_dir: Path) -> Path:
    return Path(data_dir) / _ORPHANS_FILE


def record_orphan(data_dir: Path, owner_uid: str, doc_id: str) -> None:
    """Append an orphaned doc dir to the cleanup queue (idempotent)."""
    path = _orphans_path(data_dir)
    orphans = list_orphans(data_dir)
    if any(o["owner_uid"] == owner_uid and o["doc_id"] == doc_id for o in orphans):
        return
    orphans.append({"owner_uid": owner_uid, "doc_id": doc_id})
    path.write_text(json.dumps(orphans))


def list_orphans(data_dir: Path) -> list[dict[str, Any]]:
    path = _orphans_path(data_dir)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def clear_orphan(data_dir: Path, owner_uid: str, doc_id: str) -> None:
    orphans = [o for o in list_orphans(data_dir)
               if not (o.get("owner_uid") == owner_uid and o.get("doc_id") == doc_id)]
    _orphans_path(data_dir).write_text(json.dumps(orphans))


def sweep_orphans(data_dir: Path) -> int:
    """Retry cleanup of recorded orphans. Returns the number cleared."""
    import shutil
    cleared = 0
    for o in list_orphans(data_dir):
        doc_dir = Path(data_dir) / o.get("owner_uid", "") / o.get("doc_id", "")
        try:
            if doc_dir.exists():
                shutil.rmtree(doc_dir)
            clear_orphan(data_dir, o.get("owner_uid", ""), o.get("doc_id", ""))
            cleared += 1
        except OSError:
            continue
    return cleared
