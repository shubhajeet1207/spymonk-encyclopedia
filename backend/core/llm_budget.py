"""Process-wide ceiling on chat-model calls per UTC day.

A safety net against runaway spend: research runs fan out into many LLM
calls, and an unauthenticated or misbehaving client could otherwise loop
them. The counter is in memory, so it resets when the API restarts.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from . import config


class LLMBudgetExceeded(RuntimeError):
    """The configured daily LLM call limit has been reached."""


class DailyCallBudget:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._day = ""
        self._used = 0

    def _today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def consume(self, calls: int = 1) -> None:
        limit = config.LLM_DAILY_CALL_LIMIT
        with self._lock:
            today = self._today()
            if today != self._day:
                self._day, self._used = today, 0
            if limit and self._used + calls > limit:
                raise LLMBudgetExceeded(
                    "The daily LLM call limit has been reached. Try again tomorrow or raise LLM_DAILY_CALL_LIMIT."
                )
            self._used += calls

    def used_today(self) -> int:
        with self._lock:
            return self._used if self._day == self._today() else 0

    def reset(self) -> None:
        with self._lock:
            self._day, self._used = "", 0


budget = DailyCallBudget()
