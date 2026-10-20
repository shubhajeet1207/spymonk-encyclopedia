"""No-network regression tests for pinned, deadline-bounded web fetches."""

from __future__ import annotations

import os
import socket
import unittest
from unittest.mock import patch

from backend.core import scraper


class _SocketFixture:
    def __init__(self, response: bytes) -> None:
        self._response = bytearray(response)
        self.connected_to: tuple[str, int] | None = None
        self.sent = bytearray()
        self.closed = False

    def settimeout(self, _timeout: float) -> None:
        pass

    def setsockopt(self, *_option: object) -> None:
        pass

    def connect(self, destination: tuple[str, int]) -> None:
        self.connected_to = destination

    def send(self, buffer: bytes) -> int:
        self.sent.extend(buffer)
        return len(buffer)

    def recv(self, max_bytes: int) -> bytes:
        body = bytes(self._response[:max_bytes])
        del self._response[:max_bytes]
        return body

    def close(self) -> None:
        self.closed = True


class PinnedFetchTests(unittest.TestCase):
    @staticmethod
    def _dns_result(address: str) -> tuple:
        return (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 80))

    def test_rebinding_cannot_change_the_connected_peer(self) -> None:
        sock = _SocketFixture(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n\r\nok"
        )
        with (
            patch.object(
                scraper.socket,
                "getaddrinfo",
                side_effect=[
                    [self._dns_result("8.8.8.8")],
                    [self._dns_result("127.0.0.1")],
                ],
            ) as resolver,
            patch.object(scraper.socket, "socket", return_value=sock),
            patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:3128"}),
        ):
            final_url, body, content_type = scraper.fetch_url("http://example.org/report")

        self.assertEqual(resolver.call_count, 1)
        self.assertEqual(sock.connected_to, ("8.8.8.8", 80))
        self.assertIn(b"Host: example.org", sock.sent)
        self.assertEqual((final_url, body, content_type), ("http://example.org/report", b"ok", "text/plain"))
        self.assertTrue(sock.closed)

    def test_redirect_to_private_address_is_rejected_before_connect(self) -> None:
        sock = _SocketFixture(
            b"HTTP/1.1 302 Found\r\nLocation: http://169.254.169.254/latest\r\nContent-Length: 0\r\n\r\n"
        )
        with (
            patch.object(scraper.socket, "getaddrinfo", return_value=[self._dns_result("8.8.8.8")]) as resolver,
            patch.object(scraper.socket, "socket", return_value=sock) as socket_factory,
        ):
            with self.assertRaisesRegex(ValueError, "Blocked non-public"):
                scraper.fetch_url("http://example.org/start")

        resolver.assert_called_once()
        socket_factory.assert_called_once()
        self.assertEqual(sock.connected_to, ("8.8.8.8", 80))

    def test_https_keeps_original_host_for_sni_and_http_host(self) -> None:
        sock = _SocketFixture(
            b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"
        )
        sni_hosts: list[str | None] = []

        def pretend_tls(stream, _context, server_hostname=None, timeout=None):
            sni_hosts.append(server_hostname)
            return stream

        with (
            patch.object(scraper.socket, "getaddrinfo", return_value=[self._dns_result("8.8.8.8")]),
            patch.object(scraper.socket, "socket", return_value=sock),
            patch.object(scraper._SocketStream, "start_tls", pretend_tls),
        ):
            final_url, body, _content_type = scraper.fetch_url("https://example.org/report")

        self.assertEqual(sock.connected_to, ("8.8.8.8", 443))
        self.assertEqual(sni_hosts, ["example.org"])
        self.assertIn(b"Host: example.org", sock.sent)
        self.assertEqual((final_url, body), ("https://example.org/report", b"ok"))

    def test_nonstandard_port_is_rejected_before_dns_or_connection(self) -> None:
        with patch.object(scraper.socket, "getaddrinfo") as resolver:
            with self.assertRaisesRegex(ValueError, "Blocked non-public"):
                scraper.fetch_url("https://example.org:8080/report")
        resolver.assert_not_called()

    def test_each_stream_operation_uses_remaining_total_deadline(self) -> None:
        class FakeStream:
            def __init__(self) -> None:
                self.timeouts: list[float] = []

            def read(self, _max_bytes: int, timeout: float) -> bytes:
                self.timeouts.append(timeout)
                return b"x"

        upstream = FakeStream()
        clock = [100.0]
        with patch.object(scraper.time, "monotonic", side_effect=lambda: clock[0]):
            stream = scraper._DeadlineStream(upstream, 105.0)
            self.assertEqual(stream.read(1, timeout=25.0), b"x")
            clock[0] = 104.0
            self.assertEqual(stream.read(1, timeout=25.0), b"x")
            clock[0] = 105.0
            with self.assertRaises(TimeoutError):
                stream.read(1, timeout=25.0)
        self.assertEqual(upstream.timeouts, [5.0, 1.0])


if __name__ == "__main__":
    unittest.main()
