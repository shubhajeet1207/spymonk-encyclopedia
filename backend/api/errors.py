"""Safe HTTP error reporting helpers."""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request

from backend.observability.logging import log_internal_error


def internal_error(
    logger: logging.Logger,
    request: Request,
    operation: str,
    exc: Exception,
    *,
    status_code: int = 500,
) -> HTTPException:
    """Log a stack trace without exception text, then return a safe API error."""
    request_id = getattr(request.state, "request_id", "unknown")
    return HTTPException(
        status_code=status_code,
        detail=log_internal_error(logger, operation, exc, request_id),
    )
