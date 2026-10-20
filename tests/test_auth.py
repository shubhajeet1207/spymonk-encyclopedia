"""SEC-01: API access must not rely on a browser Origin header."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.core import config, memory, store
from backend.main import app
from backend.services import rate_limit


class ApiAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        rate_limit.reset_all()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        for patcher in (
            patch.object(config, "PDF_DIR", Path(temporary.name)),
            patch.object(memory, "list_topics", return_value=[]),
            patch.object(store, "index_exists", return_value=False),
            patch.object(store, "clear_vectorstore"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.local = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000))
        self.remote = TestClient(app, base_url="http://localhost", client=("203.0.113.10", 50000))

    def test_all_private_routes_reject_missing_and_wrong_token(self) -> None:
        routes = [
            ("GET", "/api/status"),
            ("GET", "/api/topics"),
            ("POST", "/api/research/plan"),
            ("POST", "/api/research"),
            ("POST", "/api/research/stream"),
            ("POST", "/api/ask"),
            ("POST", "/api/documents"),
            ("DELETE", "/api/memory"),
            ("GET", "/api/memory/reindex"),
            ("POST", "/api/memory/reindex"),
            ("POST", "/api/research/jobs"),
            ("GET", "/api/research/jobs/unknown"),
            ("GET", "/api/research/jobs/unknown/events"),
            ("DELETE", "/api/research/jobs/unknown"),
        ]
        with patch.object(config, "APP_API_TOKEN", "correct-token"):
            for method, path in routes:
                for headers in ({}, {"Authorization": "Bearer wrong-token"}):
                    with self.subTest(method=method, path=path, headers=headers):
                        response = self.local.request(method, path, headers=headers, json={} if method == "POST" else None)
                        self.assertEqual(response.status_code, 401)
                        self.assertEqual(response.headers.get("www-authenticate"), "Bearer")

        store.clear_vectorstore.assert_not_called()

    def test_valid_token_allows_get_and_cli_write_without_origin(self) -> None:
        with patch.object(config, "APP_API_TOKEN", "correct-token"):
            headers = {"Authorization": "bEaReR correct-token"}
            status = self.local.get("/api/status", headers=headers)
            topics = self.local.get("/api/topics", headers=headers)
            cleared = self.local.delete("/api/memory", headers=headers)

        self.assertEqual(status.status_code, 200)
        self.assertEqual(topics.status_code, 200)
        self.assertEqual(cleared.status_code, 200)
        store.clear_vectorstore.assert_called_once()

    def test_unset_token_is_local_only_and_health_is_public(self) -> None:
        with patch.object(config, "APP_API_TOKEN", ""):
            self.assertEqual(self.local.get("/api/status").status_code, 200)
            self.assertEqual(self.remote.get("/api/status").status_code, 403)
            self.assertEqual(self.remote.get("/api/health").status_code, 200)

    def test_origin_guard_remains_separate_from_bearer_auth(self) -> None:
        with patch.object(config, "APP_API_TOKEN", "correct-token"):
            response = self.local.delete(
                "/api/memory",
                headers={"Authorization": "Bearer correct-token", "Origin": "https://untrusted.example"},
            )

        self.assertEqual(response.status_code, 403)
        store.clear_vectorstore.assert_not_called()

    def test_cors_preflight_allows_authorization_header(self) -> None:
        with patch.object(config, "APP_API_TOKEN", "correct-token"):
            response = self.local.options(
                "/api/research",
                headers={
                    "Origin": config.ALLOWED_ORIGINS[0],
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "authorization,content-type",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("authorization", response.headers["access-control-allow-headers"].lower())

    def test_api_security_headers_are_sent_on_success_and_auth_failure(self) -> None:
        with patch.object(config, "APP_API_TOKEN", "correct-token"):
            responses = [self.local.get("/api/health"), self.local.get("/api/status")]

        self.assertEqual([response.status_code for response in responses], [200, 401])
        for response in responses:
            # JSON responses never load content, so the API policy allows nothing.
            self.assertIn("default-src 'none'", response.headers["content-security-policy"])
            self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
            self.assertEqual(response.headers["x-content-type-options"], "nosniff")
            self.assertEqual(response.headers["referrer-policy"], "no-referrer")


if __name__ == "__main__":
    unittest.main()
