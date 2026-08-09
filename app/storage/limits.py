"""Storage limit configuration — env-overridable, defaults preserved."""

import os


def _mb(value: str | None, default_mb: int) -> int:
    if value is None:
        return default_mb * 1024 * 1024
    try:
        return int(float(value) * 1024 * 1024)
    except ValueError:
        return default_mb * 1024 * 1024


def max_upload_bytes() -> int:
    """Per-file upload cap in bytes (env MAX_UPLOAD_MB, default 200)."""
    return _mb(os.environ.get("MAX_UPLOAD_MB"), 200)


def user_storage_limit() -> int:
    """Per-user storage quota in bytes (env USER_STORAGE_GB, default 1)."""
    value = os.environ.get("USER_STORAGE_GB")
    if value is None:
        return 1024 * 1024 * 1024
    try:
        return int(float(value) * 1024 * 1024 * 1024)
    except ValueError:
        return 1024 * 1024 * 1024
