from pathlib import Path
import os


def user_data_dir(data_dir: Path, user_id: str) -> Path:
    p = data_dir / user_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def user_db_path(db_dir: Path, user_id: str) -> Path:
    return db_dir / f"{user_id}.sqlite"


def validate_user_path(data_dir: Path, user_id: str, target: str) -> Path:
    user_root = (data_dir / user_id).resolve()
    resolved = Path(target).resolve()
    if not str(resolved).startswith(str(user_root) + os.sep) and resolved != user_root:
        raise ValueError(f"path outside user tree: {target}")
    return resolved