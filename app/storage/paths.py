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
    p = Path(target)
    if not p.is_absolute():
        p = data_dir / user_id / p
    resolved = p.resolve()
    try:
        resolved.relative_to(user_root)
    except ValueError:
        raise ValueError(f"path outside user tree: {target}")
    if p.is_symlink() or any(parent.is_symlink() for parent in p.parents):
        real = os.path.realpath(str(p))
        real_path = Path(real)
        try:
            real_path.relative_to(str(user_root))
        except ValueError:
            raise ValueError(f"symlink target outside user tree: {target}")
    return resolved