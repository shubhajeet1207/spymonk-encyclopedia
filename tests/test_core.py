"""Focused regression tests for source verification and topic research."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ddgs.exceptions import DDGSException
from langchain_core.documents import Document

from backend.core import embeddings, query, research, scraper, search_planner, store, verify
from backend.core.scraper import FetchedPage, SearchHit, SearchProviderUnavailableError
from backend.core.verify import quote_is_supported


class QuoteVerificationTests(unittest.TestCase):
    def test_quote_requires_a_normalized_verbatim_match(self) -> None:
        quote = "This supported statement is deliberately long enough to pass the minimum quote threshold."
        source = "Opening text.  This   supported statement is deliberately long enough\n to pass the minimum quote threshold. Closing text."

        self.assertTrue(quote_is_supported(quote, source))
        self.assertFalse(quote_is_supported(quote, "A different source entirely."))


class ScraperTests(unittest.TestCase):
    class _Response:
        def __init__(self, url: str, *, location: str | None = None, body: bytes = b"") -> None:
            self.url = url
            self.is_redirect = location is not None
            self.headers = {"location": location} if location else {"content-type": "application/pdf"}
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self):
            yield self._body

    class _Client:
        def __init__(self, responses):
            self.responses = iter(responses)
            self.urls: list[str] = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def stream(self, _method: str, url: str):
            self.urls.append(url)
            return next(self.responses)

    def test_fetch_url_rechecks_a_redirect_target(self) -> None:
        client = self._Client(
            [
                self._Response("https://example.org/start", location="/report.pdf"),
                self._Response("https://example.org/report.pdf", body=b"%PDF-test"),
            ]
        )
        with (
            patch.object(scraper, "_client", return_value=client),
            patch.object(
                scraper,
                "_resolve_public_http_url",
                return_value=scraper._FetchTarget("example.org", 443, "8.8.8.8"),
            ),
        ):
            url, body, content_type = scraper.fetch_url("https://example.org/start")

        self.assertEqual(client.urls, ["https://example.org/start", "https://example.org/report.pdf"])
        self.assertEqual(url, "https://example.org/report.pdf")
        self.assertEqual(body, b"%PDF-test")
        self.assertEqual(content_type, "application/pdf")

    def test_snapshots_are_content_addressed_and_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(scraper.config, "WEB_PDF_DIR", Path(directory)):
            first = scraper.save_pdf("https://example.org/report.pdf", b"%PDF-first")
            second = scraper.save_pdf("https://other.example/report.pdf", b"%PDF-second")
            again = scraper.save_pdf("https://mirror.example/report.pdf", b"%PDF-first")

            self.assertTrue(first.name.startswith("report-"))
            self.assertEqual(first.suffix, ".pdf")
            self.assertNotEqual(first, second)
            self.assertEqual(first, again)
            self.assertEqual(first.read_bytes(), b"%PDF-first")
            self.assertEqual(second.read_bytes(), b"%PDF-second")

    def test_search_topic_sources_uses_the_supplied_plan_and_deduplicates_hits(self) -> None:
        first = SearchHit("One", "https://example.org/one", "first")
        duplicate = SearchHit("One duplicate", "https://example.org/one", "duplicate")
        with patch.object(scraper, "search_web", side_effect=[[first], [duplicate]]) as search:
            hits = scraper.search_topic_sources(
                "topic",
                queries=["  topic primary source  ", "topic secondary source"],
            )

        self.assertEqual(search.call_args_list[0].args[0], "topic primary source")
        self.assertEqual(search.call_args_list[1].args[0], "topic secondary source")
        self.assertEqual(hits, [first])

    def test_failed_query_is_logged_and_remaining_queries_continue(self) -> None:
        hit = SearchHit("Working result", "https://example.org/working", "result")
        queries = ["rate limited query", "working query"]
        with (
            patch.object(
                scraper,
                "search_web",
                side_effect=[
                    DDGSException("https://search.brave.com/search returned HTTP 429 Too Many Requests"),
                    [hit],
                ],
            ),
            self.assertLogs(scraper.logger, level="WARNING") as captured,
        ):
            hits = scraper.search_topic_sources("topic", queries=queries)

        self.assertEqual(hits, [hit])
        expected_hash = hashlib.sha256(queries[0].encode("utf-8")).hexdigest()[:12]
        self.assertIn(f"query_hash={expected_hash}", captured.output[0])
        self.assertNotIn(queries[0], captured.output[0])
        self.assertIn("query_index=1/2", captured.output[0])
        self.assertIn("reason=rate_limited", captured.output[0])
        self.assertIn("provider_host=search.brave.com", captured.output[0])
        self.assertIn("http_status=429", captured.output[0])
        self.assertIn("fallback=continue", captured.output[0])

    def test_all_failed_search_queries_raise_a_provider_unavailable_error(self) -> None:
        with (
            patch.object(
                scraper,
                "search_web",
                side_effect=DDGSException("No results found."),
            ),
            self.assertLogs(scraper.logger, level="WARNING"),
            self.assertRaises(SearchProviderUnavailableError) as raised,
        ):
            scraper.search_topic_sources("topic", queries=["first query", "second query"])

        self.assertEqual(raised.exception.query_count, 2)
        self.assertEqual(raised.exception.failed_query_count, 2)


class SearchPlannerTests(unittest.TestCase):
    def test_llm_query_plan_is_parsed_and_deduplicated(self) -> None:
        from types import SimpleNamespace

        class FakeLLM:
            def invoke(self, _messages):
                return SimpleNamespace(
                    content='```json\n{"queries":["topic primary source", "topic scholarly source", "topic primary source"]}\n```'
                )

        with patch.object(search_planner, "get_llm", return_value=FakeLLM()):
            plan = search_planner.plan_search_queries("topic")

        self.assertEqual(plan["planner"], "llm")
        self.assertEqual(plan["queries"], ["topic primary source", "topic scholarly source"])
        self.assertFalse(plan["fallback_used"])

    def test_unusable_llm_plan_uses_explicit_baseline_fallback(self) -> None:
        from types import SimpleNamespace

        class FakeLLM:
            def invoke(self, _messages):
                return SimpleNamespace(content="not json")

        with patch.object(search_planner, "get_llm", return_value=FakeLLM()):
            plan = search_planner.plan_search_queries("topic")

        self.assertEqual(plan["planner"], "baseline-fallback")
        self.assertEqual(len(plan["queries"]), 3)
        self.assertTrue(plan["fallback_used"])


class ResearchWorkflowTests(unittest.TestCase):
    def test_research_writes_only_a_quote_verified_claim(self) -> None:
        quote = "This is a sufficiently long verbatim sentence from the source that supports the resulting claim."
        source_text = f"{'Preface. ' * 16}{quote} {'Epilogue. ' * 12}"
        hits = [
            SearchHit("Example report", "https://example.org/report.pdf", "report"),
            SearchHit("Independent report", "https://research.example.net/report.pdf", "report"),
        ]
        pipeline_events = []

        with tempfile.TemporaryDirectory() as directory:
            pdfs = []
            for index, hit in enumerate(hits):
                pdf_path = Path(directory) / f"report-{index}.pdf"
                pdf_path.write_bytes(b"%PDF-placeholder")
                pdfs.append(
                    FetchedPage(
                        url=hit.url,
                        title=f"report-{index}.pdf",
                        content_type="application/pdf",
                        text="",
                        local_path=pdf_path,
                    )
                )
            with (
                patch.object(research.config, "LLM_API_KEY", "test-key"),
                patch.object(research.config, "DATA_DIR", Path(directory)),
                patch.object(research.config, "MEMORY_DIR", Path(directory) / "memory/topics"),
                patch.object(research.config, "CHROMA_DIR", Path(directory) / "chroma_db"),
                patch.object(research, "search_topic_sources", return_value=hits),
                patch.object(research, "collect_research_sources", return_value=([], [hit.url for hit in hits])),
                patch.object(research, "download_reference_pdfs", return_value=pdfs),
                patch.object(research, "_source_text", return_value=source_text),
                patch.object(
                    research,
                    "_extract_from_text",
                    return_value=[
                        {"claim": "A supported claim.", "quote": quote, "page": None},
                        {"claim": "An unsupported claim.", "quote": "not present " * 10, "page": None},
                    ],
                ),
                patch.object(
                    research,
                    "_compare_claims_with_source",
                    side_effect=lambda claims, _source, _text: [
                        {"verdict": "supports", "quote": quote, "reason": "Direct quote support."}
                        for _ in claims
                    ],
                ),
                patch.object(research, "replace_topic_web_documents") as upsert,
                patch.object(research, "save_topic", return_value=Path(directory) / "topic.json"),
            ):
                archive = research.research_topic("Test topic", on_progress=pipeline_events.append)

        self.assertEqual([claim["claim"] for claim in archive["claims"]], ["A supported claim."])
        self.assertTrue(archive["claims"][0]["verified"])
        self.assertEqual(
            {"search", "collect", "extract", "verify", "save"},
            {event["step"] for event in pipeline_events},
        )
        self.assertEqual("complete", pipeline_events[-1]["status"])
        upsert.assert_called_once()


class ArchiveLookupTests(unittest.TestCase):
    def test_exact_topic_lookup_uses_metadata_without_loading_embeddings(self) -> None:
        collection = MagicMock()
        collection.get.return_value = {
            "documents": ["Supported claim", "Unresolved claim"],
            "metadatas": [
                {"topic": "Test topic", "origin": "web", "validation_status": "corroborated"},
                {"topic": "Test topic", "origin": "web", "validation_status": "single_source"},
            ],
        }
        client = MagicMock()
        client.get_collection.return_value = collection
        self.addCleanup(store.close_clients)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        with (
            patch.object(store.config, "CHROMA_DIR", Path(directory.name) / "chroma_db"),
            patch.object(store.config, "VECTOR_STORE_PROVIDER", "chroma-local"),
            patch.object(store, "index_exists", return_value=True),
            patch("chromadb.PersistentClient", return_value=client),
            patch.object(embeddings, "get_embeddings") as embedding_factory,
        ):
            documents = store.find_topic_documents("Test topic")

        self.assertEqual([document.page_content for document in documents], ["Supported claim"])
        client.get_collection.assert_called_once_with(
            name=store.config.COLLECTION_NAME, embedding_function=None
        )
        collection.get.assert_called_once_with(
            where={
                "$and": [
                    {"origin": "web"},
                    {"validation_status": {"$in": ["corroborated", "pdf_resolved"]}},
                    {"$or": [{"topic_key": "test topic"}, {"topic": "Test topic"}]},
                ]
            },
            limit=store.config.MAX_CLAIMS_PER_TOPIC,
            include=["documents", "metadatas"],
        )
        embedding_factory.assert_not_called()

    def test_reverification_reads_each_source_once_per_request(self) -> None:
        quote = "A directly quoted statement that is sufficiently long to pass the verification length rule."
        evidence = [
            {
                "verdict": "supports",
                "quote": quote,
                "origin": origin,
                "source_url": f"https://{origin}/report",
                "source_type": "web_html",
            }
            for origin in ("first.example.com", "second.example.net")
        ]
        documents = [
            Document(
                page_content=f"Claim {index}",
                metadata={
                    "origin": "web",
                    "validation_status": "corroborated",
                    "evidence_json": json.dumps(evidence),
                },
            )
            for index in range(2)
        ]
        with patch.object(verify, "source_text_from_record", return_value=quote) as read_source:
            retained = query.reverify_documents(documents)

        self.assertEqual(retained, documents)
        self.assertEqual(read_source.call_count, 2)


if __name__ == "__main__":
    unittest.main()
