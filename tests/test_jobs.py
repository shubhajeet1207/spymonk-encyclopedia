"""ARCH-01/SEC-02: research runs as bounded, cancellable, replayable jobs."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.core import config, research, store
from backend.core.research import ResearchCancelled
from backend.main import app
from backend.services import rate_limit
from backend.services.jobs import JobManager, JobRejected, get_job_manager

logger = logging.getLogger("tests.jobs")


def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        time.sleep(0.01)


class JobManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = JobManager(max_workers=1, max_queued=0)
        self.addCleanup(self.manager.shutdown)

    def test_same_topic_returns_running_job_and_capacity_is_enforced(self) -> None:
        release = threading.Event()
        job, existing = self.manager.submit(topic="Docker", request_id="r1", runner=lambda _job: release.wait(5) and {})
        again, again_existing = self.manager.submit(topic="  docker ", request_id="r2", runner=lambda _job: {})

        self.assertFalse(existing)
        self.assertTrue(again_existing)
        self.assertIs(again, job)
        with self.assertRaises(JobRejected) as rejected:
            self.manager.submit(topic="Other topic", request_id="r3", runner=lambda _job: {})
        self.assertEqual(rejected.exception.status_code, 429)
        release.set()
        self.assertTrue(job.wait(5))
        self.assertEqual(job.status, "succeeded")

    def test_cancel_stops_a_running_job(self) -> None:
        def runner(job):
            while True:
                job.check_cancelled()
                time.sleep(0.01)

        job, _ = self.manager.submit(topic="Long", request_id="r1", runner=runner)
        _wait_until(lambda: job.status == "running")
        self.manager.cancel(job.id)

        self.assertTrue(job.wait(5))
        self.assertEqual(job.status, "cancelled")
        self.assertEqual(job.error["status"], 409)

    def test_deadline_times_out_a_running_job(self) -> None:
        def runner(job):
            while True:
                job.check_cancelled()
                time.sleep(0.01)

        with patch.object(config, "RESEARCH_MAX_SECONDS", 0):
            job, _ = self.manager.submit(topic="Slow", request_id="r1", runner=runner)
            self.assertTrue(job.wait(5))

        self.assertEqual(job.status, "cancelled")
        self.assertEqual(job.error["status"], 504)

    def test_events_replay_from_any_position_and_end_with_the_result(self) -> None:
        def runner(job):
            for index in range(3):
                job.add_event("pipeline", {"step": "search", "index": index})
            return {"cached": False}

        job, _ = self.manager.submit(topic="Replay", request_id="r1", runner=runner)
        self.assertTrue(job.wait(5))
        events, terminal = job.wait_events(1, timeout=0)

        self.assertTrue(terminal)
        self.assertEqual([index for index, _, _ in events], [1, 2, 3])
        self.assertEqual(events[-1][1], "result")

    def test_job_logs_carry_the_submitting_request_id(self) -> None:
        from backend.observability.logging import RequestIdFilter

        with self.assertLogs(logger, level="INFO") as captured:
            captured_handler = logger.handlers[-1]
            captured_handler.addFilter(RequestIdFilter())
            job, _ = self.manager.submit(
                topic="Logged", request_id="request-123", runner=lambda _job: logger.info("inside job") or {}
            )
            job.wait(5)

        self.assertEqual(captured.records[0].request_id, "request-123")

    def test_failure_message_includes_request_id(self) -> None:
        def runner(_job):
            raise RuntimeError("provider exploded with secret details")

        job, _ = self.manager.submit(topic="Broken", request_id="request-456", runner=runner)
        job.wait(5)

        self.assertEqual(job.status, "failed")
        self.assertEqual(job.error["status"], 502)
        self.assertIn("request-456", job.error["message"])
        self.assertNotIn("secret", job.error["message"])


class ResearchJobApiTests(unittest.TestCase):
    def setUp(self) -> None:
        rate_limit.reset_all()
        self.manager = JobManager(max_workers=1, max_queued=2)
        app.dependency_overrides[get_job_manager] = lambda: self.manager
        self.addCleanup(app.dependency_overrides.clear)
        self.addCleanup(self.manager.shutdown)
        self.client = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000))

    def test_job_lifecycle_over_http(self) -> None:
        archive = {"topic": "topic", "claims": [], "unresolved_claims": [], "sources": []}

        def fake_research(topic, status=None, *, search_queries=None, on_progress=None, check_cancelled=None, trace=None):
            on_progress({"step": "search", "status": "active", "message": "Searching."})
            return archive

        with (
            patch.object(store, "index_exists", return_value=False),
            patch.object(research, "research_topic", side_effect=fake_research),
        ):
            created = self.client.post("/api/research/jobs", json={"topic": "topic", "search_queries": ["topic source"]})
            job_id = created.json()["job_id"]
            self.manager.get(job_id).wait(5)
            status = self.client.get(f"/api/research/jobs/{job_id}")
            events = self.client.get(f"/api/research/jobs/{job_id}/events?after=0")

        self.assertEqual(created.status_code, 202)
        self.assertEqual(created.headers["location"], f"/api/research/jobs/{job_id}")
        self.assertEqual(status.json()["status"], "succeeded")
        self.assertEqual(status.json()["result"]["archive"]["topic"], "topic")
        self.assertIn('"step":"search","status":"active"', events.text)
        self.assertIn("event: result", events.text)
        self.assertEqual(self.client.get("/api/research/jobs/unknown").status_code, 404)

    def test_delete_cancels_a_running_job(self) -> None:
        started = threading.Event()

        def fake_research(topic, status=None, *, search_queries=None, on_progress=None, check_cancelled=None, trace=None):
            started.set()
            while True:
                check_cancelled()
                time.sleep(0.01)

        with (
            patch.object(store, "index_exists", return_value=False),
            patch.object(research, "research_topic", side_effect=fake_research),
        ):
            job_id = self.client.post("/api/research/jobs", json={"topic": "topic", "search_queries": ["q1"]}).json()["job_id"]
            self.assertTrue(started.wait(5))
            cancelled = self.client.delete(f"/api/research/jobs/{job_id}")
            self.manager.get(job_id).wait(5)

        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(self.manager.get(job_id).status, "cancelled")

    def test_closing_the_legacy_stream_cancels_its_job(self) -> None:
        from backend.api.routes.research import _event_stream

        def runner(job):
            job.add_event("pipeline", {"step": "search", "status": "active"})
            while True:
                job.check_cancelled()
                time.sleep(0.01)

        job, _ = self.manager.submit(topic="Streaming", request_id="r1", runner=runner)

        async def read_one_then_disconnect() -> str:
            stream = _event_stream(job, self.manager, cancel_on_disconnect=True)
            first = await stream.__anext__()
            await stream.aclose()
            return first

        first = asyncio.run(read_one_then_disconnect())
        self.assertTrue(job.wait(5))
        self.assertIn("event: pipeline", first)
        self.assertEqual(job.status, "cancelled")


class ResearchCancellationTests(unittest.TestCase):
    def test_cancelled_research_saves_nothing(self) -> None:
        calls = {"count": 0}

        def check() -> None:
            calls["count"] += 1
            if calls["count"] > 1:
                raise ResearchCancelled("cancelled")

        with (
            patch.object(config, "LLM_API_KEY", "test-key"),
            patch.object(research, "search_topic_sources", return_value=[research.SearchHit("t", "https://a.example/x", "")]),
            patch.object(research, "collect_research_sources") as collect,
            patch.object(research, "replace_topic_web_documents") as replace,
            patch.object(research, "save_topic") as save,
        ):
            with self.assertRaises(ResearchCancelled):
                research.research_topic("Topic", check_cancelled=check)

        collect.assert_not_called()
        replace.assert_not_called()
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
