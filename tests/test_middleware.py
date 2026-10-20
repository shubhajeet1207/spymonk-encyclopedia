"""Tests for HTTP request middleware behavior."""

from __future__ import annotations

import asyncio
import unittest

from backend.main import RequestLoggingMiddleware
from backend.observability.logging import request_id_context


class RequestLoggingMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancellation_propagates_and_resets_request_context(self) -> None:
        async def cancelled_app(scope, receive, send):
            raise asyncio.CancelledError

        async def unused_receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def unused_send(message):
            return None

        middleware = RequestLoggingMiddleware(cancelled_app)
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/cancelled-test",
            "headers": [(b"x-request-id", b"middleware-cancel-test")],
        }

        with self.assertRaises(asyncio.CancelledError):
            await middleware(scope, unused_receive, unused_send)

        self.assertEqual(request_id_context.get(), "-")
        self.assertEqual(scope["state"]["request_id"], "middleware-cancel-test")


if __name__ == "__main__":
    unittest.main()
