"""Regression tests for public-address filtering in web fetching."""

from __future__ import annotations

import ipaddress
import unittest
from unittest.mock import patch

from backend.core import scraper


class PublicIPTests(unittest.TestCase):
    def test_public_ip_classification(self) -> None:
        cases = {
            "8.8.8.8": True,
            "2606:4700:4700::1111": True,
            "127.0.0.1": False,
            "10.0.0.1": False,
            "100.64.0.1": False,
            "169.254.169.254": False,
            "192.0.2.1": False,
            "224.0.0.1": False,
            "240.0.0.1": False,
            "0.0.0.0": False,
            "::1": False,
            "fc00::1": False,
            "fe80::1": False,
            "fec0::1": False,
            "ff02::1": False,
            "2001:db8::1": False,
            "2606:4700:4700::1111%eth0": False,
            "::ffff:8.8.8.8": True,
            "::ffff:169.254.169.254": False,
            "2002:808:808::1": True,
            "2002:a9fe:a9fe::1": False,
            "2001:0:a9fe:a9fe:0:0:f7f7:f7f7": False,  # Teredo server
            "2001:0:808:808:0:0:5601:5601": False,  # Teredo client
            "64:ff9b::808:808": True,
            "64:ff9b::a9fe:a9fe": False,
            "64:ff9b:1:808:8:800::": True,
            "64:ff9b:1:808:8:800::1": False,  # Noncanonical /48 suffix
            "64:ff9b:1:a9fe:a9:fe00::": False,
            "not-an-address": False,
        }
        for raw, expected in cases.items():
            with self.subTest(address=raw):
                self.assertEqual(scraper.is_public_ip(raw), expected)
                if raw != "not-an-address":
                    self.assertEqual(scraper.is_public_ip(ipaddress.ip_address(raw)), expected)

    def test_teredo_checks_both_embedded_ipv4_addresses(self) -> None:
        # The IPv6 Teredo range is itself non-global in Python's registry.
        # Isolate the embedded-address rule so a future registry change cannot
        # silently turn a private Teredo endpoint into an allowed destination.
        def simulated_public(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
            return isinstance(address, ipaddress.IPv6Address) or address.is_global

        with patch.object(scraper, "_address_is_public", side_effect=simulated_public):
            self.assertTrue(scraper.is_public_ip("2001:0:808:808:0:0:f7f7:f7f7"))
            self.assertFalse(scraper.is_public_ip("2001:0:a9fe:a9fe:0:0:f7f7:f7f7"))
            self.assertFalse(scraper.is_public_ip("2001:0:808:808:0:0:5601:5601"))

    def test_public_url_rejects_any_unsafe_resolution(self) -> None:
        def result(address: str):
            return (0, 0, 0, "", (address, 443))

        with patch.object(scraper.socket, "getaddrinfo", return_value=[result("8.8.8.8"), result("64:ff9b::a9fe:a9fe")]):
            self.assertFalse(scraper._is_public_http_url("https://example.com/report"))

        with patch.object(scraper.socket, "getaddrinfo", return_value=[]):
            self.assertFalse(scraper._is_public_http_url("https://example.com/report"))

        with patch.object(scraper.socket, "getaddrinfo", return_value=[result("8.8.8.8"), result("2606:4700:4700::1111")]):
            self.assertTrue(scraper._is_public_http_url("https://example.com/report"))


if __name__ == "__main__":
    unittest.main()
