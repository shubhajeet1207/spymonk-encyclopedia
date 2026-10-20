"""Chat-provider failures are reported as what they are, not as internal errors."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx
import openai
from fastapi.testclient import TestClient

from backend.core import config, query, search_planner, store
from backend.main import app
from backend.services import archive, rate_limit
from backend.services.jobs import JobManager

_REQUEST = httpx.Request("POST", "https://llm.example/v1/chat/completions")


def _status_error(cls, status: int):
    return cls("provider said no", response=httpx.Response(status, request=_REQUEST), body=None)


class ProviderFailureMappingTests(unittest.TestCase):
    def test_each_provider_failure_has_a_specific_status_and_message(self) -> None:
        cases = [
            (openai.APITimeoutError(request=_REQUEST), 504, "did not respond within"),
            (openai.APIConnectionError(request=_REQUEST), 502, "Could not connect"),
            (_status_error(openai.RateLimitError, 429), 429, "rate-limiting"),
            (_status_error(openai.AuthenticationError, 401), 502, "rejected the configured API key"),
            (_status_error(openai.NotFoundError, 404), 502, "is not available to this account"),
        ]
        with patch.object(config, "LLM_MODEL", "vendor/test-model"), patch.object(config, "LLM_TIMEOUT_SECONDS", 60.0):
            for exc, status, text in cases:
                with self.subTest(error=type(exc).__name__):
                    code, detail = archive.describe_failure(exc, "request-1")
                    self.assertEqual(code, status)
                    self.assertIn(text, detail)
                    self.assertNotIn("provider said no", detail)

    def test_timeout_names_the_model_and_the_limit(self) -> None:
        with patch.object(config, "LLM_MODEL", "vendor/slow-model"), patch.object(config, "LLM_TIMEOUT_SECONDS", 45.0):
            _, detail = archive.describe_failure(openai.APITimeoutError(request=_REQUEST), "request-1")
        self.assertIn("vendor/slow-model", detail)
        self.assertIn("45 seconds", detail)


class ProviderFailureApiTests(unittest.TestCase):
    def setUp(self) -> None:
        rate_limit.reset_all()
        self.client = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000))

    def test_search_planning_timeout_is_504_with_guidance(self) -> None:
        with patch.object(search_planner, "plan_search_queries", side_effect=openai.APITimeoutError(request=_REQUEST)):
            response = self.client.post("/api/research/plan", json={"topic": "topic"})

        self.assertEqual(response.status_code, 504)
        self.assertIn("did not respond within", response.json()["detail"])

    def test_answer_timeout_fails_only_the_compose_stage(self) -> None:
        from langchain_core.documents import Document

        document = Document(page_content="Saved claim", metadata={"origin": "web", "validation_status": "corroborated"})
        manager = JobManager(max_workers=1, max_queued=1)
        self.addCleanup(manager.shutdown)

        def run(job):
            return archive.lookup_or_research(
                "topic",
                reverify=False,
                on_progress=lambda event: job.add_event("pipeline", event),
                check_cancelled=job.check_cancelled,
            )

        with (
            patch.object(store, "index_exists", return_value=True),
            patch.object(store, "find_topic_documents", return_value=[document]),
            patch.object(query, "answer_from_documents", side_effect=openai.APITimeoutError(request=_REQUEST)),
        ):
            job, _ = manager.submit(topic="topic", request_id="request-9", runner=run)
            self.assertTrue(job.wait(5))

        events, _ = job.wait_events(0, timeout=0)
        failed = [data["step"] for _, kind, data in events if kind == "pipeline" and data.get("status") == "failed"]
        self.assertEqual(failed, ["compose"])
        self.assertEqual(job.error["status"], 504)
        self.assertIn("did not respond within", job.error["message"])
        self.assertIn("request-9", job.error["message"])


if __name__ == "__main__":
    unittest.main()
