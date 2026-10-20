"""Facts about one research run, collected for the unanswered-question reports.

The trace records what the pipeline did (queries, sources, model calls,
verdicts, timings) without prompts, full source text, or provider error text.
Extraction and cross-check workers record into it from several threads.
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any

_MAX_EVENTS = 200


class ResearchTrace:
    def __init__(self) -> None:
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._started = time.perf_counter()
        self._lock = threading.Lock()
        self._stage_started: dict[str, float] = {}
        self.stage_ms: dict[str, float] = {}
        self.events: list[dict[str, Any]] = []
        self.counters: Counter[str] = Counter()
        self.plan: dict[str, Any] = {}
        self.search: dict[str, Any] = {"queries": [], "results": []}
        self.collection: dict[str, Any] = {}
        self.sources: list[dict[str, Any]] = []
        self.extractions: dict[str, dict[str, Any]] = {}
        self.candidates: dict[str, Any] = {}
        self.cross_checks: list[dict[str, Any]] = []
        self.claims: dict[str, dict[str, Any]] = {}

    def elapsed_ms(self) -> float:
        return round((time.perf_counter() - self._started) * 1000, 1)

    def event(self, event: dict[str, object]) -> None:
        """Record a pipeline event and time the stage it opens or closes."""
        step = event.get("step")
        state = event.get("status")
        now = time.perf_counter()
        with self._lock:
            if isinstance(step, str) and state == "active":
                self._stage_started.setdefault(step, now)
            elif isinstance(step, str) and state in {"complete", "skipped", "failed"}:
                started = self._stage_started.pop(step, None)
                if started is not None:
                    self.stage_ms[step] = round((now - started) * 1000, 1)
            if len(self.events) < _MAX_EVENTS:
                self.events.append(
                    {
                        "t_ms": self.elapsed_ms(),
                        "step": step,
                        "status": state,
                        "message": str(event.get("message") or "")[:300],
                    }
                )

    def count(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self.counters[name] += amount

    def record_extraction(self, url: str, **fields: Any) -> None:
        with self._lock:
            self.extractions.setdefault(url, {}).update(fields)

    def record_cross_check(self, entry: dict[str, Any]) -> None:
        with self._lock:
            self.cross_checks.append(entry)
