"""API authentication and browser-origin checks."""

from __future__ import annotations

import hmac
import logging
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

import backend.core.config as config

logger = logging.getLogger(__name__)


def require_api_access(request: Request) -> None:
    """Require a bearer token, or restrict tokenless local mode to loopback."""
    configured_token = config.APP_API_TOKEN
    if configured_token:
        authorization = request.headers.get("authorization", "")
        parts = authorization.split(None, 1)
        if (
            len(parts) == 2
            and parts[0].lower() == "bearer"
            and hmac.compare_digest(parts[1].encode("utf-8"), configured_token.encode("utf-8"))
        ):
            return
        raise HTTPException(
            status_code=401,
            detail="A valid API token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    client_host = request.client.host if request.client else ""
    if client_host in {"127.0.0.1", "::1"}:
        return
    raise HTTPException(
        status_code=403,
        detail="The API is local-only until APP_API_TOKEN is configured.",
    )


def require_allowed_origin(request: Request) -> None:
    """Reject cross-site writes unless the exact frontend origin is configured."""
    origin = request.headers.get("origin")
    if not origin:
        return  # Non-browser clients commonly omit Origin.

    allowed = set(config.ALLOWED_ORIGINS)
    if origin in allowed:
        return

    parsed = urlsplit(origin)
    hostname = (parsed.hostname or "").lower()
    request_host = request.headers.get("host", "").lower()
    request_origin = f"{request.url.scheme}://{request_host}"
    if (
        hostname in {"127.0.0.1", "localhost", "::1"}
        and origin == request_origin
        and parsed.scheme == request.url.scheme
    ):
        return

    logger.warning(
        "Rejected browser write from an unallowed origin (origin_host=%s)",
        hostname or "unknown",
    )
    raise HTTPException(
        status_code=403,
        detail="Requests must originate from an allowed SpyMonk frontend.",
    )
