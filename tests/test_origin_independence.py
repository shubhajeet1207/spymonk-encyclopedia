"""Independent-origin counting must use registrable domains, not hostnames."""

from __future__ import annotations

import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import patch

from langchain_core.documents import Document

from backend.core import research, scraper, verify
from backend.core.scraper import FetchedPage, SearchHit


class RegistrableOriginTests(unittest.TestCase):
    def test_legacy_same_domain_evidence_fails_reverification(self) -> None:
        quote = "A sufficiently long quoted sentence from both pages that would otherwise look supported."
        evidence = [
            {
                "verdict": "supports",
                "quote": quote,
                "origin": origin,
                "source_url": f"https://{origin}/report",
            }
            for origin in ("www.example.com", "example.com")
        ]
        document = Document(
            page_content="Old claim",
            metadata={
                "origin": "web",
                "validation_status": "corroborated",
                "evidence_json": json.dumps(evidence),
            },
        )
        with patch.object(verify, "source_text_from_record") as read_source:
            self.assertFalse(verify.document_still_verified(document))
        read_source.assert_not_called()

    def test_private_suffixes_and_subdomains(self) -> None:
        cases = {
            "https://www.example.com/report": ("www.example.com", "example.com"),
            "https://example.com/report": ("example.com", "example.com"),
            "https://a.evil.com/report": ("a.evil.com", "evil.com"),
            "https://b.evil.com/report": ("b.evil.com", "evil.com"),
            "https://a.github.io/report": ("a.github.io", "a.github.io"),
            "https://b.github.io/report": ("b.github.io", "b.github.io"),
            "https://bücher.com/report": ("bücher.com", "xn--bcher-kva.com"),
            "https://xn--bcher-kva.com/report": ("xn--bcher-kva.com", "xn--bcher-kva.com"),
        }
        for url, (hostname, independent_origin) in cases.items():
            with self.subTest(url=url):
                self.assertEqual(scraper.source_origin(url), hostname)
                self.assertEqual(scraper.registrable_origin(url), independent_origin)

    def _research_sources(self, urls: list[str]) -> tuple[dict, int]:
        quote = "This sufficiently long verbatim sentence appears in each source and explicitly supports the claim."
        source_text = f"{'Preface. ' * 20}{quote} {'Epilogue. ' * 20}"
        pages = [FetchedPage(url, f"Source {index}", "text/html", source_text) for index, url in enumerate(urls)]
        hits = [SearchHit(page.title, page.url, "") for page in pages]

        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(research.config, "LLM_API_KEY", "test-key"),
                patch.object(research.config, "MEMORY_DIR", Path(directory) / "memory/topics"),
                patch.object(research.config, "CHROMA_DIR", Path(directory) / "chroma_db"),
                patch.object(research, "find_topic_documents", return_value=[]),
                patch.object(research, "search_topic_sources", return_value=hits),
                patch.object(research, "collect_research_sources", return_value=(pages, [])),
                patch.object(research, "download_reference_pdfs", return_value=[]),
                patch.object(research, "_extract_from_text", return_value=[{"claim": "A supported claim.", "quote": quote, "page": None}]),
                patch.object(
                    research,
                    "_compare_claims_with_source",
                    side_effect=lambda claims, _source, _text: [
                        {"verdict": "supports", "quote": quote, "reason": "Direct support."} for _ in claims
                    ],
                ),
                patch.object(research, "replace_topic_web_documents") as replace,
                patch.object(research, "save_topic", return_value=Path("unused-archive.json")),
            ):
                archive = research.research_topic("Test topic")
        # A run that verified nothing records an unanswered question instead of indexing.
        return archive, len(replace.call_args.args[1]) if replace.called else 0

    def test_only_distinct_registrable_domains_promote_claims(self) -> None:
        cases = [
            (["https://www.example.com/one", "https://example.com/two"], [], "single_source"),
            (["https://a.evil.com/one", "https://b.evil.com/two"], [], "single_source"),
            (["https://a.github.io/one", "https://b.github.io/two"], ["a.github.io", "b.github.io"], "corroborated"),
            (["https://a.evil.com/one", "https://b.evil.com/two", "https://example.net/three"], ["evil.com", "example.net"], "corroborated"),
        ]
        for urls, expected_promoted_origins, expected_status in cases:
            with self.subTest(urls=urls):
                archive, indexed_count = self._research_sources(urls)
                claims = archive["claims"] or archive["unresolved_claims"]
                self.assertEqual(len(claims), 1)
                self.assertEqual(claims[0]["validation_status"], expected_status)
                self.assertEqual(len(archive["claims"]), indexed_count)
                if expected_promoted_origins:
                    self.assertEqual(claims[0]["supporting_origins"], expected_promoted_origins)
                else:
                    self.assertEqual(len(claims[0]["supporting_origins"]), 1)
                self.assertEqual(
                    [source["origin"] for source in archive["sources"]],
                    [scraper.source_origin(url) for url in urls],
                )


if __name__ == "__main__":
    unittest.main()
