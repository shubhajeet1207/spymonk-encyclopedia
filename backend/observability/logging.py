"""Request-correlated logging with a safe, consistent console and file format."""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import traceback
import uuid
from contextvars import ContextVar

request_id_context: ContextVar[str] = ContextVar("spymonk_request_id", default="-")
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_FORMAT = "%(asctime)s %(levelname)s [request_id=%(request_id)s] %(name)s: %(message)s"
_FILE_HANDLER_NAME = "spymonk-file"


class RequestIdFilter(logging.Filter):
    """Attach the current request ID to application and propagated log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_context.get()
        return True


def new_request_id(candidate: str | None = None) -> str:
    """Reuse a simple caller-provided ID, or generate an opaque safe ID."""
    value = (candidate or "").strip()
    return value if _REQUEST_ID_PATTERN.fullmatch(value) else uuid.uuid4().hex


def safe_exception_context(exc: Exception) -> str:
    """Return useful exception metadata without serializing its message/body."""
    parts = [f"error_type={type(exc).__name__}"]
    response = getattr(exc, "response", None)
    status_code = getattr(exc, "status_code", None) or getattr(response, "status_code", None)
    if isinstance(status_code, int) and 100 <= status_code <= 599:
        parts.append(f"http_status={status_code}")
    error_code = getattr(exc, "code", None)
    if isinstance(error_code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", error_code):
        parts.append(f"error_code={error_code}")
    error_number = getattr(exc, "errno", None)
    if isinstance(error_number, int):
        parts.append(f"errno={error_number}")
    return " ".join(parts)


def log_internal_error(
    logger: logging.Logger, operation: str, exc: BaseException, request_id: str
) -> str:
    """Log a stack trace without exception text; return a safe user-facing detail.

    Exception messages can contain user prompts, provider responses, or full
    source URLs. Stack frames plus the exception type are enough to locate the
    failing code while keeping those values out of the default logs.
    """
    stack = "".join(traceback.format_tb(exc.__traceback__)).rstrip()
    logger.error(
        "%s failed (%s)\n%s",
        operation,
        safe_exception_context(exc),  # type: ignore[arg-type]
        stack or "<no traceback available>",
    )
    return f"{operation} failed. Check backend logs using request ID {request_id}."


def configure_logging() -> None:
    """Configure console logging without exposing request payloads by default."""
    configured_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, configured_level, None)
    if not isinstance(level, int):
        level = logging.INFO

    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(handler)
    for handler in root.handlers:
        if not any(isinstance(item, RequestIdFilter) for item in handler.filters):
            handler.addFilter(RequestIdFilter())
    root.setLevel(level)

    http_client_level_name = os.getenv("HTTPX_LOG_LEVEL", "").strip().upper()
    if http_client_level_name:
        http_client_level = getattr(logging, http_client_level_name, logging.WARNING)
        if not isinstance(http_client_level, int):
            http_client_level = logging.WARNING
    else:
        # Successful model-download requests are noisy at INFO. App-level
        # fallback/error logs still report failed downloads and HTTP statuses.
        http_client_level = logging.INFO if level <= logging.DEBUG else logging.WARNING
    for logger_name in ("httpx", "httpx2", "httpcore"):
        logging.getLogger(logger_name).setLevel(http_client_level)


def enable_file_logging(directory: Path, *, max_bytes: int, backups: int) -> Path:
    """Also write the log, in the same safe format, to a rotating file.

    Called by the process that holds the data-directory lock, so one writer
    owns the file; importing the app (as tests do) never opens it.
    """
    root = logging.getLogger()
    path = directory / "spymonk-api.log"
    if any(handler.get_name() == _FILE_HANDLER_NAME for handler in root.handlers):
        return path
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8", delay=True)
    handler.set_name(_FILE_HANDLER_NAME)
    handler.setFormatter(logging.Formatter(_FORMAT))
    handler.addFilter(RequestIdFilter())
    root.addHandler(handler)
    return path


def disable_file_logging() -> None:
    """Close the log file, so a reloaded process can rotate it."""
    root = logging.getLogger()
    for handler in [item for item in root.handlers if item.get_name() == _FILE_HANDLER_NAME]:
        root.removeHandler(handler)
        handler.close()
