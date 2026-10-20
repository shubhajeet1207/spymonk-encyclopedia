"""A failed or empty refresh must not replace previously verified memory."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.documents import Document

from backend.core import config, inquiries, memory, research
from backend.core.scraper import FetchedPage, SearchHit


class ResearchPreservationTests(unittest.TestCase):
    def _run_with_failures(
        self, *, extraction_failure: bool = False, no_claims: bool = False
    ) -> tuple[dict, object, object, bool, dict]:
        quote = "This verifiable source sentence is intentionally long enough for the quote matching rule."
        source_text = f"{'Opening context. ' * 15}{quote} {'Closing context. ' * 15}"
        hits = [
            SearchHit("First", "https://first.example/report", "snippet"),
            SearchHit("Second", "https://second.example/report", "snippet"),
        ]
        pages = [
            FetchedPage(url=hit.url, title=hit.title, content_type="text/html", text=source_text)
            for hit in hits
        ]
        previous = Document(
            page_content="Previously verified claim",
            metadata={"origin": "web", "validation_status": "corroborated"},
        )
        extracted = [{"claim": "A claim from the source.", "quote": quote, "page": None}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(config, "LLM_API_KEY", "test-key"),
                patch.object(config, "CHROMA_DIR", root / "chroma"),
                patch.object(config, "MEMORY_DIR", root / "topics"),
                patch.object(config, "PDF_DIR", root / "pdfs"),
                patch.object(config, "WEB_HTML_DIR", root / "html"),
                patch.object(config, "WEB_PDF_DIR", root / "web_pdfs"),
                patch.object(research, "search_topic_sources", return_value=hits),
                patch.object(research, "collect_research_sources", return_value=(pages, [])),
                patch.object(research, "download_reference_pdfs", return_value=[]),
                patch.object(research, "_source_text", return_value=source_text),
                patch.object(research, "find_topic_documents", return_value=[previous], create=True),
                patch.object(
                    research,
                    "_extract_from_text",
                    side_effect=RuntimeError("test") if extraction_failure else None,
                    return_value=[] if no_claims else extracted,
                ),
                patch.object(research, "_compare_claims_with_source", side_effect=RuntimeError("test")),
                patch.object(research, "replace_topic_web_documents") as replace,
                patch.object(research, "save_topic", return_value=root / "topic.json") as save,
            ):
                old_path = memory.save_topic({"topic": "Existing topic", "claims": [{"id": "old-claim"}]})
                old_bytes = old_path.read_bytes()
                archive = research.research_topic("Existing topic")
                old_intact = old_path.read_bytes() == old_bytes
                record = inquiries.load_inquiry(archive["inquiry_id"])
        return archive, replace, save, old_intact, record

    def test_cross_check_failures_preserve_previous_verified_claims(self) -> None:
        archive, replace, save, old_intact, record = self._run_with_failures(extraction_failure=False)
        self.assertFalse(archive["memory_updated"])
        self.assertTrue(old_intact)
        self.assertEqual(archive["previous_verified_claims"], 1)
        self.assertEqual(record["outcome"]["reason"], "processing_failure")
        self.assertEqual(record["outcome"]["previous_verified_claims"], 1)
        self.assertEqual(record["outcome"]["failure_counts"]["cross_check"], 2)
        replace.assert_not_called()
        save.assert_not_called()

    def test_extraction_failures_preserve_previous_verified_claims(self) -> None:
        archive, replace, save, old_intact, record = self._run_with_failures(extraction_failure=True)
        self.assertFalse(archive["memory_updated"])
        self.assertTrue(old_intact)
        self.assertEqual(record["outcome"]["reason"], "processing_failure")
        self.assertEqual(record["outcome"]["failure_counts"]["extraction"], 2)
        replace.assert_not_called()
        save.assert_not_called()

    def test_zero_verified_claims_preserve_previous_verified_claims(self) -> None:
        archive, replace, save, old_intact, record = self._run_with_failures(no_claims=True)
        self.assertFalse(archive["memory_updated"])
        self.assertTrue(old_intact)
        self.assertEqual(record["outcome"]["reason"], "no_candidate_claims")
        self.assertEqual(record["outcome"]["previous_verified_claims"], 1)
        replace.assert_not_called()
        save.assert_not_called()

    def test_malformed_llm_output_is_not_treated_as_no_claims(self) -> None:
        from types import SimpleNamespace

        class FakeLLM:
            def invoke(self, _messages):
                return SimpleNamespace(content="not JSON")

        with patch.object(research, "get_llm", return_value=FakeLLM()), self.assertRaises(ValueError):
            research._extract_from_text("Topic", "https://first.example/report", "Title", "Source text")


if __name__ == "__main__":
    unittest.main()
