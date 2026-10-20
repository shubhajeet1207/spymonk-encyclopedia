"""Boundary checks must run before API handlers parse request bodies."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.main import app
from backend.core import config


class HttpGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000))

    def test_untrusted_host_is_rejected_before_health_route(self) -> None:
        response = self.client.get("/api/health", headers={"Host": "attacker.example"})
        self.assertEqual(response.status_code, 400)

    def test_local_host_is_allowed(self) -> None:
        response = self.client.get("/api/health", headers={"Host": "localhost:8000"})
        self.assertEqual(response.status_code, 200)

    def test_oversized_json_content_length_is_rejected_before_parsing(self) -> None:
        response = self.client.post(
            "/api/research/plan",
            content=b"x" * (1024 * 1024 + 1),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)

    def test_streamed_json_without_length_is_rejected_while_reading(self) -> None:
        chunks = (b"x" * 512_000 for _ in range(3))
        response = self.client.post(
            "/api/research/plan",
            content=chunks,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)

    def test_upload_limit_rejects_declared_body_before_multipart_parsing(self) -> None:
        with patch.object(config, "MAX_PDF_BYTES", 1):
            response = self.client.post(
                "/api/documents",
                content=b"",
                headers={
                    "Content-Type": "multipart/form-data; boundary=test",
                    "Content-Length": str(1024 * 1024 + 11),
                },
            )
        self.assertEqual(response.status_code, 413)


if __name__ == "__main__":
    unittest.main()
