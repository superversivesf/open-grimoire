"""Structured logging for pipeline and agent operations.

Writes to logs/rpg-master.log with timestamps and per-job/per-session context.
"""
import logging
import sys
from pathlib import Path

_loggers = {}

def get_logger(name: str, log_dir: str = "logs") -> logging.Logger:
    if name in _loggers:
        return _loggers[name]
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.handlers = []
    fh = logging.FileHandler(log_path / "rpg-master.log")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    logger.addHandler(sh)
    _loggers[name] = logger
    return logger