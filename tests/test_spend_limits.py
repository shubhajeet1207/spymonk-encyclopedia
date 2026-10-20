"""SEC-02: request rate limits and the daily LLM call ceiling."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from backend.core import config, query, search_planner
from backend.core.llm_budget import LLMBudgetExceeded, budget
from backend.main import app
from backend.services import rate_limit
from backend.services.rate_limit import TokenBucketLimiter


class RateLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        rate_limit.reset_all()
        self.addCleanup(rate_limit.reset_all)
        self.client = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000))

    def test_bucket_allows_burst_then_reports_wait(self) -> None:
        limiter = TokenBucketLimiter("test", lambda: 2)
        self.assertEqual(limiter.try_acquire("client"), 0.0)
        self.assertEqual(limiter.try_acquire("client"), 0.0)
        self.assertGreater(limiter.try_acquire("client"), 0.0)
        self.assertEqual(limiter.try_acquire("other-client"), 0.0)

    def test_research_endpoints_return_429_with_retry_after(self) -> None:
        plan = {"queries": ["a query", "b query"], "planner": "llm", "fallback_used": False}
        with (
            patch.object(config, "RATE_LIMIT_RESEARCH_PER_MINUTE", 1),
            patch.object(search_planner, "plan_search_queries", return_value=plan),
        ):
            first = self.client.post("/api/research/plan", json={"topic": "topic"})
            second = self.client.post("/api/research/plan", json={"topic": "topic"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertGreaterEqual(int(second.headers["retry-after"]), 1)


class LlmBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        budget.reset()
        self.addCleanup(budget.reset)
        rate_limit.reset_all()

    def test_daily_ceiling_stops_further_model_calls(self) -> None:
        model = MagicMock()
        with (
            patch.object(config, "LLM_DAILY_CALL_LIMIT", 2),
            patch.object(config, "LLM_API_KEY", "test-key"),
            patch.object(query, "ChatOpenAI", return_value=model),
        ):
            llm = query.get_llm()
            llm.invoke("one")
            llm.invoke("two")
            with self.assertRaises(LLMBudgetExceeded):
                llm.invoke("three")

        self.assertEqual(model.invoke.call_count, 2)

    def test_zero_disables_the_ceiling(self) -> None:
        with patch.object(config, "LLM_DAILY_CALL_LIMIT", 0):
            for _ in range(5):
                budget.consume()

    def test_exhausted_budget_is_reported_as_429(self) -> None:
        client = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000))
        with patch.object(search_planner, "plan_search_queries", side_effect=LLMBudgetExceeded("limit reached")):
            response = client.post("/api/research/plan", json={"topic": "topic"})

        self.assertEqual(response.status_code, 429)
        self.assertIn("limit", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
