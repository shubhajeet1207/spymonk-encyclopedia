"""In-process research jobs: bounded concurrency, cancellation, and progress replay.

A research run takes minutes and dozens of model calls, so it runs as a job on
a small worker pool instead of inside an HTTP request. Clients watch its
events, reconnect without losing progress, and cancel it explicitly. Jobs live
in memory: this API runs as a single instance (see instance_lock).
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from backend.core import config
from backend.core.memory import canonical_topic
from backend.core.research import ResearchCancelled
from backend.observability.logging import request_id_context

from .archive import describe_failure

logger = logging.getLogger(__name__)

TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled"})
_MAX_EVENTS = 1000
_RETENTION_SECONDS = 3600
_MAX_RETAINED_JOBS = 100


class JobRejected(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class ResearchJob:
    def __init__(self, topic: str, request_id: str) -> None:
        self.id = uuid.uuid4().hex
        self.topic = topic
        self.topic_key = canonical_topic(topic)
        self.request_id = request_id
        self.status = "queued"
        self.created_at = time.time()
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.result: dict | None = None
        self.error: dict | None = None
        self._deadline: float | None = None
        self._cancel = threading.Event()
        self._changed = threading.Condition()
        self._events: list[tuple[str, object]] = []
        self._offset = 0

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATES

    def add_event(self, kind: str, data: object) -> None:
        with self._changed:
            self._events.append((kind, data))
            if len(self._events) > _MAX_EVENTS:
                # Keep the newest progress; readers that fell behind skip ahead.
                dropped = len(self._events) - _MAX_EVENTS
                del self._events[:dropped]
                self._offset += dropped
            self._changed.notify_all()

    def wait_events(self, after: int, timeout: float) -> tuple[list[tuple[int, str, object]], bool]:
        """Events with index >= after, waiting up to `timeout` for new ones.

        The boolean is True when the job has finished and every event up to
        and including its result or error has been returned.
        """
        with self._changed:
            if self._offset + len(self._events) <= after and not self.terminal:
                self._changed.wait(timeout)
            start = max(after, self._offset)
            items = [
                (index, kind, data)
                for index, (kind, data) in enumerate(self._events[start - self._offset :], start=start)
            ]
            return items, self.terminal

    def check_cancelled(self) -> None:
        if self._cancel.is_set():
            raise ResearchCancelled("cancelled")
        if self._deadline is not None and time.monotonic() > self._deadline:
            raise ResearchCancelled("timed out")

    def request_cancel(self) -> None:
        self._cancel.set()

    def _start(self) -> None:
        with self._changed:
            self.status = "running"
            self.started_at = time.time()
            self._deadline = time.monotonic() + config.RESEARCH_MAX_SECONDS
            self._changed.notify_all()

    def _finish(self, status: str, *, result: dict | None = None, error: dict | None = None) -> None:
        with self._changed:
            if result is not None:
                self._events.append(("result", result))
            if error is not None:
                self._events.append(("error", {"message": error["message"], "status": error["status"]}))
            self.result = result
            self.error = error
            self.status = status
            self.finished_at = time.time()
            self._changed.notify_all()

    def wait(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._changed:
            while not self.terminal:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._changed.wait(remaining)
            return True

    def snapshot(self) -> dict[str, Any]:
        with self._changed:
            return {
                "job_id": self.id,
                "topic": self.topic,
                "status": self.status,
                "created_at": self.created_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "event_count": self._offset + len(self._events),
                "result": self.result,
                "error": self.error,
            }


Runner = Callable[[ResearchJob], dict]


class JobManager:
    def __init__(self, *, max_workers: int | None = None, max_queued: int | None = None) -> None:
        self._max_workers = max_workers or config.RESEARCH_MAX_CONCURRENT_JOBS
        self._max_queued = config.RESEARCH_MAX_QUEUED_JOBS if max_queued is None else max_queued
        self._executor = ThreadPoolExecutor(
            max_workers=self._max_workers, thread_name_prefix="research-job"
        )
        self._jobs: OrderedDict[str, ResearchJob] = OrderedDict()
        self._active_by_topic: dict[str, str] = {}
        self._lock = threading.Lock()
        self._closed = False

    def _prune(self) -> None:
        now = time.time()
        for job_id in list(self._jobs):
            job = self._jobs[job_id]
            expired = job.terminal and job.finished_at and now - job.finished_at > _RETENTION_SECONDS
            if expired or (job.terminal and len(self._jobs) > _MAX_RETAINED_JOBS):
                del self._jobs[job_id]

    def submit(self, *, topic: str, request_id: str, runner: Runner) -> tuple[ResearchJob, bool]:
        """Queue a job; an unfinished job for the same topic is returned instead."""
        with self._lock:
            if self._closed:
                raise JobRejected(503, "The research service is shutting down.")
            self._prune()
            key = canonical_topic(topic)
            active_id = self._active_by_topic.get(key)
            active = self._jobs.get(active_id) if active_id else None
            if active is not None and not active.terminal:
                return active, True
            unfinished = sum(1 for job in self._jobs.values() if not job.terminal)
            if unfinished >= self._max_workers + self._max_queued:
                raise JobRejected(
                    429,
                    "Research capacity is full. Wait for a running research job to finish, then try again.",
                )
            job = ResearchJob(topic, request_id)
            self._jobs[job.id] = job
            self._active_by_topic[key] = job.id
        self._executor.submit(self._run, job, runner)
        logger.info("Research job queued (job_id=%s unfinished_jobs=%s)", job.id, unfinished + 1)
        return job, False

    def _run(self, job: ResearchJob, runner: Runner) -> None:
        token = request_id_context.set(job.request_id)
        try:
            if job._cancel.is_set():
                job._finish("cancelled", error={"status": 409, "detail": "Research was cancelled before it started.", "message": "Research was cancelled before it started."})
                return
            job._start()
            logger.info("Research job started (job_id=%s)", job.id)
            result = runner(job)
            job._finish("succeeded", result=result)
            logger.info("Research job succeeded (job_id=%s)", job.id)
        except BaseException as exc:  # Every failure must finish the job for waiting clients.
            status_code, detail = describe_failure(exc, job.request_id)
            message = detail if job.request_id in detail else f"{detail} (request ID: {job.request_id})"
            state = "cancelled" if isinstance(exc, ResearchCancelled) else "failed"
            job._finish(state, error={"status": status_code, "detail": detail, "message": message})
            logger.info("Research job %s (job_id=%s status=%s)", state, job.id, status_code)
            if not isinstance(exc, Exception):
                raise
        finally:
            with self._lock:
                if self._active_by_topic.get(job.topic_key) == job.id:
                    del self._active_by_topic[job.topic_key]
            request_id_context.reset(token)

    def get(self, job_id: str) -> ResearchJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> ResearchJob | None:
        job = self.get(job_id)
        if job is not None and not job.terminal:
            job.request_cancel()
            logger.info("Research job cancellation requested (job_id=%s)", job.id)
        return job

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            jobs = list(self._jobs.values())
        for job in jobs:
            job.request_cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)


_default_manager: JobManager | None = None
_default_lock = threading.Lock()


def get_job_manager() -> JobManager:
    """FastAPI dependency; tests may override it with app.dependency_overrides."""
    global _default_manager
    with _default_lock:
        if _default_manager is None or _default_manager._closed:
            _default_manager = JobManager()
        return _default_manager


def shutdown_job_manager() -> None:
    global _default_manager
    with _default_lock:
        manager, _default_manager = _default_manager, None
    if manager is not None:
        manager.shutdown()
