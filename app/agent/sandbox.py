from pathlib import Path
from app.storage.paths import validate_user_path


def safe_read_file(data_dir: Path, user_id: str, path: str, lines: str | None = None) -> str:
    full = validate_user_path(data_dir, user_id, path)
    if not full.exists():
        return f"(file not found: {path})"
    text = full.read_text()
    if lines:
        try:
            start, end = map(int, lines.split("-"))
            text_lines = text.splitlines()
            text = "\n".join(text_lines[start : end + 1])
        except (ValueError, IndexError):
            pass
    return truncate_result(text)


def truncate_result(text: str, max_chars: int = 16000) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[... file is long, use read_file(path, lines='N-M') for more]"


def safe_ls(data_dir: Path, user_id: str, dir_path: str) -> list[str]:
    full = validate_user_path(data_dir, user_id, dir_path)
    if not full.is_dir():
        return []
    return sorted(p.name for p in full.iterdir())