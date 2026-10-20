"""API tests for the explicit LLM search-planning stage."""

from __future__ import annotations

import hashlib
import json
import unittest
from unittest.mock import patch

from ddgs.exceptions import DDGSException
from fastapi.testclient import TestClient
from langchain_core.documents import Document

from backend.core import config, query, research, scraper, search_planner, store
from backend.core.query import EncyclopediaAnswer
from backend.core.scraper import SearchProviderUnavailableError
from backend.main import app
from backend.observability.logging import RequestIdFilter
from backend.services import rate_limit


class SearchPlanningApiTests(unittest.TestCase):
    def setUp(self) -> None:
        rate_limit.reset_all()
        self.client = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000))

    def test_plan_endpoint_calls_llm_planner_without_searching(self) -> None:
        plan = {
            "queries": ["topic primary source", "topic scholarly reference"],
            "planner": "llm",
            "fallback_used": False,
        }
        with patch.object(search_planner, "plan_search_queries", return_value=plan) as planner:
            response = self.client.post(
                "/api/research/plan",
                json={"topic": "topic"},
                headers={"X-Request-ID": "plan-api-test"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Request-ID"], "plan-api-test")
        self.assertEqual(response.json()["queries"], plan["queries"])
        planner.assert_called_once_with("topic")

    def test_research_endpoint_executes_a_supplied_plan(self) -> None:
        plan_queries = ["topic primary source", "topic scholarly reference"]
        archive = {"topic": "topic", "claims": [], "unresolved_claims": [], "sources": []}
        captured: dict[str, list[str] | None] = {}

        def fake_research(topic, status=None, *, search_queries=None, on_progress=None, check_cancelled=None, trace=None):
            captured["queries"] = search_queries
            if on_progress:
                on_progress({"step": "search", "status": "active", "message": "Searching test plan."})
                on_progress({"step": "search", "status": "complete", "message": "Search complete."})
            return archive

        with (
            patch.object(store, "index_exists", return_value=False),
            patch.object(research, "research_topic", side_effect=fake_research),
            patch.object(search_planner, "plan_search_queries") as planner,
        ):
            response = self.client.post(
                "/api/research",
                json={"topic": "topic", "search_queries": plan_queries},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["queries"], plan_queries)
        self.assertEqual(response.json()["search_plan"]["planner"], "provided")
        planner.assert_not_called()

    def test_research_stream_reports_live_pipeline_events_and_result(self) -> None:
        archive = {"topic": "topic", "claims": [], "unresolved_claims": [], "sources": []}

        def fake_research(topic, status=None, *, search_queries=None, on_progress=None, check_cancelled=None, trace=None):
            if on_progress:
                on_progress({"step": "search", "status": "active", "message": "Searching query 1 of 1."})
                on_progress({"step": "search", "status": "complete", "message": "Found 2 results."})
            return archive

        with (
            patch.object(store, "index_exists", return_value=False),
            patch.object(research, "research_topic", side_effect=fake_research),
        ):
            response = self.client.post(
                "/api/research/stream",
                json={"topic": "topic", "search_queries": ["topic source"]},
                headers={"X-Request-ID": "stream-research-test"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers["content-type"])
        self.assertEqual(response.headers["x-request-id"], "stream-research-test")
        self.assertIn('event: pipeline\ndata: {"step":"memory"', response.text)
        self.assertIn('"step":"search","status":"active"', response.text)
        self.assertIn('event: result\ndata: {"cached":false', response.text)

    def test_research_stream_finishes_archive_check_before_answer_generation(self) -> None:
        document = Document(
            page_content="A verified claim",
            metadata={"origin": "web", "validation_status": "corroborated"},
        )
        cached_answer = EncyclopediaAnswer(answer="Answer from archive.", sources=[])
        with (
            patch.object(store, "index_exists", return_value=True),
            patch.object(store, "find_topic_documents", return_value=[document]) as exact_lookup,
            patch.object(query, "retrieve_eligible_documents") as semantic_lookup,
            patch.object(query, "reverify_documents", return_value=[document]) as reverify,
            patch.object(query, "answer_from_documents", return_value=cached_answer) as compose,
            patch.object(research, "research_topic") as web_research,
        ):
            response = self.client.post(
                "/api/research/stream",
                json={"topic": "topic"},
            )

        self.assertEqual(response.status_code, 200)
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        pipeline = [event for event in events if "step" in event]
        self.assertEqual(
            [(event["step"], event["status"]) for event in pipeline[:5]],
            [
                ("memory", "active"),
                ("memory", "complete"),
                ("retrieve", "active"),
                ("retrieve", "complete"),
                ("reverify", "active"),
            ],
        )
        self.assertIn("duration_ms", pipeline[1])
        self.assertEqual(events[-1]["cached"], True)
        exact_lookup.assert_called_once_with("topic")
        semantic_lookup.assert_not_called()
        reverify.assert_called_once()
        compose.assert_called_once()
        web_research.assert_not_called()

    def test_research_uses_semantic_search_when_exact_topic_is_absent(self) -> None:
        document = Document(page_content="Relevant PDF excerpt", metadata={"origin": "local_pdf"})
        with (
            patch.object(store, "index_exists", return_value=True),
            patch.object(store, "find_topic_documents", return_value=[]),
            patch.object(query, "retrieve_eligible_documents", return_value=[document]) as semantic_lookup,
            patch.object(query, "answer_from_documents", return_value=EncyclopediaAnswer("PDF answer.", [])),
            patch.object(research, "research_topic") as web_research,
        ):
            response = self.client.post(
                "/api/research/stream",
                json={"topic": "topic", "reverify": False},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("via semantic search", response.text)
        self.assertIn('"step":"reverify","status":"skipped"', response.text)
        semantic_lookup.assert_called_once_with("topic")
        web_research.assert_not_called()

    def test_research_stream_marks_the_failed_stage_and_returns_request_id(self) -> None:
        def fail_search(topic, status=None, *, search_queries=None, on_progress=None, check_cancelled=None, trace=None):
            if on_progress:
                on_progress({"step": "search", "status": "active", "message": "Searching query 1 of 1."})
            raise SearchProviderUnavailableError(query_count=1, failed_query_count=1)

        with (
            patch.object(store, "index_exists", return_value=False),
            patch.object(research, "research_topic", side_effect=fail_search),
        ):
            response = self.client.post(
                "/api/research/stream",
                json={"topic": "topic", "search_queries": ["topic source"]},
                headers={"X-Request-ID": "stream-research-failure"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn('"step":"search","status":"failed"', response.text)
        self.assertIn("event: error", response.text)
        self.assertIn("request ID: stream-research-failure", response.text)

    def test_research_endpoint_returns_service_unavailable_when_all_queries_fail(self) -> None:
        with (
            patch.object(store, "index_exists", return_value=False),
            patch.object(
                research,
                "research_topic",
                side_effect=SearchProviderUnavailableError(query_count=2, failed_query_count=2),
            ),
        ):
            response = self.client.post(
                "/api/research",
                json={"topic": "topic", "search_queries": ["first query", "second query"]},
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("temporarily unavailable", response.json()["detail"])

    def test_search_failure_log_is_correlated_and_hashes_the_query(self) -> None:
        with (
            patch.object(store, "index_exists", return_value=False),
            patch.object(config, "LLM_API_KEY", "test-key"),
            patch.object(
                scraper,
                "search_web",
                side_effect=DDGSException("https://search.brave.com/search returned HTTP 429 Too Many Requests"),
            ),
            self.assertLogs(scraper.logger, level="WARNING") as captured,
        ):
            scraper.logger.handlers[0].addFilter(RequestIdFilter())
            response = self.client.post(
                "/api/research",
                json={"topic": "topic", "search_queries": ["docker bridge network query"]},
                headers={"X-Request-ID": "search-failure-api-test"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["X-Request-ID"], "search-failure-api-test")
        log_text = "\n".join(captured.output)
        self.assertEqual(captured.records[0].request_id, "search-failure-api-test")
        query_text = "docker bridge network query"
        expected_hash = hashlib.sha256(query_text.encode("utf-8")).hexdigest()[:12]
        self.assertIn(f"query_hash={expected_hash}", log_text)
        self.assertNotIn(query_text, log_text)
        self.assertIn("reason=rate_limited", log_text)
        self.assertIn("provider_host=search.brave.com", log_text)


if __name__ == "__main__":
    unittest.main()
