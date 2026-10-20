"""PERF-02/03/05 and DATA-12: bounded fetching, reuse of fetched bodies, no DNS while parsing."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.core import config, scraper
from backend.core.scraper import FetchBudget, SearchHit

HTML = b"<html><head><title>T</title></head><body>" + b"Readable words. " * 40 + b"</body></html>"


class CollectionTests(unittest.TestCase):
    def test_pdf_links_are_not_fetched_and_html_cap_stops_further_fetches(self) -> None:
        hits = [SearchHit(str(i), f"https://site{i}.org/page", "") for i in range(6)]
        hits.insert(1, SearchHit("pdf", "https://docs.org/report.pdf", ""))
        fetched: list[str] = []

        def fetch(url: str):
            fetched.append(url)
            return url, HTML, "text/html"

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(config, "WEB_HTML_DIR", Path(directory)),
            patch.object(config, "MAX_WEB_HTML", 2),
            patch.object(config, "FETCH_CONCURRENCY", 1),
            patch.object(scraper, "fetch_url", side_effect=fetch),
        ):
            pages, pdf_urls = scraper.collect_research_sources(hits)

        self.assertEqual(len(pages), 2)
        self.assertEqual(fetched, ["https://site0.org/page", "https://site1.org/page"])
        self.assertEqual(pdf_urls, ["https://docs.org/report.pdf"])

    def test_pdf_found_by_content_type_is_downloaded_once(self) -> None:
        calls: list[str] = []

        def fetch(url: str):
            calls.append(url)
            return url, b"%PDF-1.4 body", "application/pdf"

        prefetched: dict = {}
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(config, "WEB_PDF_DIR", Path(directory)),
            patch.object(scraper, "fetch_url", side_effect=fetch),
        ):
            _, pdf_urls = scraper.collect_research_sources(
                [SearchHit("x", "https://site.org/view?id=1", "")], prefetched=prefetched
            )
            pdfs = scraper.download_reference_pdfs(pdf_urls, prefetched=prefetched)

        self.assertEqual(calls, ["https://site.org/view?id=1"])
        self.assertEqual(len(pdfs), 1)

    def test_exhausted_budget_stops_fetching(self) -> None:
        hits = [SearchHit(str(i), f"https://site{i}.org/page", "") for i in range(4)]
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(config, "WEB_HTML_DIR", Path(directory)),
            patch.object(config, "FETCH_CONCURRENCY", 1),
            patch.object(scraper, "fetch_url", side_effect=lambda url: (url, HTML, "text/html")) as fetch,
        ):
            scraper.collect_research_sources(hits, budget=FetchBudget(limit=10))

        self.assertEqual(fetch.call_count, 1)

    def test_html_text_extraction_never_resolves_dns(self) -> None:
        html = b'<a href="/files/report.pdf">r</a><a href="https://other.org/a.pdf">a</a><p>' + b"text " * 50 + b"</p>"
        with patch.object(scraper.socket, "getaddrinfo", side_effect=AssertionError("DNS lookup")):
            _, _, links = scraper.extract_html_text(html, "https://site.org/index.html")
            _, _, skipped = scraper.extract_html_text(html, "https://site.org/", collect_pdf_links=False)

        self.assertEqual(links, ["https://site.org/files/report.pdf", "https://other.org/a.pdf"])
        self.assertEqual(skipped, [])


class FetchLimitTests(unittest.TestCase):
    class _Response:
        def __init__(self, content_type: str, chunks: list[bytes]) -> None:
            self.url = "https://site.org/page"
            self.is_redirect = False
            self.headers = {"content-type": content_type}
            self._chunks = chunks

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self):
            yield from self._chunks

    class _Client:
        def __init__(self, response) -> None:
            self.response = response

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def stream(self, _method: str, _url: str):
            return self.response

    def _fetch(self, content_type: str, chunks: list[bytes]):
        target = scraper._FetchTarget("site.org", 443, "8.8.8.8")
        with (
            patch.object(scraper, "_resolve_public_http_url", return_value=target),
            patch.object(scraper, "_client", return_value=self._Client(self._Response(content_type, chunks))),
        ):
            return scraper.fetch_url("https://site.org/page")

    def test_html_download_stops_at_the_html_limit(self) -> None:
        megabyte = b"x" * (1024 * 1024)
        _, body, _ = self._fetch("text/html; charset=utf-8", [megabyte] * 5)
        self.assertEqual(len(body), config.MAX_HTML_BYTES)

    def test_oversized_pdf_is_rejected(self) -> None:
        with patch.object(config, "MAX_PDF_BYTES", 10):
            with self.assertRaises(ValueError):
                self._fetch("application/pdf", [b"%PDF" + b"x" * 20])


if __name__ == "__main__":
    unittest.main()
