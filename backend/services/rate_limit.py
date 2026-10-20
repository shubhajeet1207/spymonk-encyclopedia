"""Per-client token buckets for expensive endpoints.

In-process state is enough for the single-instance deployment this API
supports. Limits are read from config on every request.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable

from fastapi import HTTPException, Request

from backend.core import config

_IDLE_SECONDS = 600


class TokenBucketLimiter:
    def __init__(self, name: str, per_minute: Callable[[], int]) -> None:
        self.name = name
        self._per_minute = per_minute
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def try_acquire(self, key: str) -> float:
        """Take one token; return 0 on success or the seconds until one is free."""
        capacity = float(max(1, self._per_minute()))
        refill_per_second = capacity / 60.0
        now = time.monotonic()
        with self._lock:
            if len(self._buckets) > 1000:
                self._buckets = {
                    client: bucket
                    for client, bucket in self._buckets.items()
                    if now - bucket[1] < _IDLE_SECONDS
                }
            tokens, updated = self._buckets.get(key, (capacity, now))
            tokens = min(capacity, tokens + (now - updated) * refill_per_second)
            if tokens >= 1.0:
                self._buckets[key] = (tokens - 1.0, now)
                return 0.0
            self._buckets[key] = (tokens, now)
            return (1.0 - tokens) / refill_per_second

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


def client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def enforce(limiter: TokenBucketLimiter, request: Request) -> None:
    retry_after = limiter.try_acquire(client_key(request))
    if retry_after > 0:
        raise HTTPException(
            status_code=429,
            detail=f"Too many {limiter.name} requests. Try again in {math.ceil(retry_after)} seconds.",
            headers={"Retry-After": str(math.ceil(retry_after))},
        )


research_limiter = TokenBucketLimiter("research", lambda: config.RATE_LIMIT_RESEARCH_PER_MINUTE)
ask_limiter = TokenBucketLimiter("question", lambda: config.RATE_LIMIT_ASK_PER_MINUTE)
upload_limiter = TokenBucketLimiter("upload", lambda: config.RATE_LIMIT_UPLOAD_PER_MINUTE)
maintenance_limiter = TokenBucketLimiter("maintenance", lambda: config.RATE_LIMIT_MAINTENANCE_PER_MINUTE)


def limit_research(request: Request) -> None:
    enforce(research_limiter, request)


def limit_ask(request: Request) -> None:
    enforce(ask_limiter, request)


def limit_upload(request: Request) -> None:
    enforce(upload_limiter, request)


def limit_maintenance(request: Request) -> None:
    enforce(maintenance_limiter, request)


def reset_all() -> None:
    for limiter in (research_limiter, ask_limiter, upload_limiter, maintenance_limiter):
        limiter.reset()
