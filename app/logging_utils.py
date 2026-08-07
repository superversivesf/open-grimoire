"""Structured logging for pipeline and agent operations.

Writes to logs/rpg-master.log with timestamps and per-job/per-session context.
Uses structlog for JSON-formatted output with request_id correlation.
"""
import sys
import logging
import contextvars
from pathlib import Path
from typing import Any, Callable, MutableMapping, cast
import structlog

# A structlog processor: (logger, method_name, event_dict) -> event_dict
Processor = Callable[[Any, str, MutableMapping[str, Any]], Any]

# Context variable for request correlation
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
job_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("job_id", default=None)


def get_request_id() -> str | None:
    """Get the current request ID from context."""
    return request_id_var.get()


def set_request_id(request_id: str) -> None:
    """Set the request ID in context."""
    request_id_var.set(request_id)


def get_job_id() -> str | None:
    """Get the current job ID from context."""
    return job_id_var.get()


def set_job_id(job_id: str | None) -> None:
    """Set the job ID in context (pass None to clear)."""
    job_id_var.set(job_id)


def _add_request_id(logger: Any, method_name: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """Add request_id and job_id to all log entries."""
    req_id = request_id_var.get()
    if req_id:
        event_dict["request_id"] = req_id
    job_id = job_id_var.get()
    if job_id:
        event_dict["job_id"] = job_id
    return event_dict


def configure_logging(log_dir: str = "logs", json_output: bool = True) -> None:
    """Configure structlog for the application.

    Call once at startup. Sets up JSON output to file and human-readable output to stdout.
    """
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # Configure standard library logging to write to file
    file_handler = logging.FileHandler(log_path / "rpg-master.log")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(message)s"))

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.handlers = [file_handler]

    # Also add console handler for human-readable output
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(console_handler)

    # Configure structlog
    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        _add_request_id,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=False),
        structlog.processors.StackInfoRenderer(),
    ]

    if json_output:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a structlog logger instance."""
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))


# Backward compatibility: configure on first use
_configured = False


def _ensure_configured() -> None:
    global _configured
    if not _configured:
        configure_logging()
        _configured = True


# Legacy function for backward compatibility
def get_logger_legacy(name: str, log_dir: str = "logs") -> logging.Logger:
    """Legacy logger for code not yet migrated to structlog."""
    _ensure_configured()
    return cast(logging.Logger, structlog.get_logger(name))