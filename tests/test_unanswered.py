"""A run that verifies nothing records an unanswered question with two reports."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pypdf import PdfReader

from backend.core import config, inquiries, memory, research, store
from backend.core.scraper import FetchedPage, SearchHit
from backend.main import app
from backend.services import rate_limit, reports

QUOTE = "This supported sentence is long enough to satisfy the verbatim quote matching rule easily."
SOURCE_TEXT = f"{'Context words. ' * 15}{QUOTE} {'More context. ' * 15}"
SECRET = "sk-test-secret-value-1234567890"


class UnansweredQuestionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in (
            ("LLM_API_KEY", SECRET),
            ("DATA_DIR", self.root),
            ("MEMORY_DIR", self.root / "memory" / "topics"),
            ("CHROMA_DIR", self.root / "chroma_db"),
        ):
            self.stack.enter_context(patch.object(config, name, value))
        self.addCleanup(store.close_clients)
        self.stack.enter_context(patch.object(research, "find_topic_documents", return_value=[]))
        self.stack.enter_context(patch.object(research, "download_reference_pdfs", return_value=[]))
        self.replace = self.stack.enter_context(patch.object(research, "replace_topic_web_documents"))
        self.events: list[dict] = []

    def _single_site_run(self) -> dict:
        """Three pages from one registrable domain: claims cannot be corroborated."""
        urls = [f"https://docs.example.org/page-{index}" for index in range(3)]
        hits = [SearchHit(f"Page {index}", url, "") for index, url in enumerate(urls)]
        pages = [FetchedPage(hit.url, hit.title, "text/html", SOURCE_TEXT) for hit in hits]
        with (
            patch.object(research, "search_topic_sources", return_value=hits),
            patch.object(research, "collect_research_sources", return_value=(pages, [])),
            patch.object(
                research,
                "_extract_from_text",
                return_value=[{"claim": "A claim only one site makes.", "quote": QUOTE, "page": None}],
            ),
            patch.object(
                research,
                "_compare_claims_with_source",
                side_effect=lambda claims, _source, _text: [
                    {"verdict": "supports", "quote": QUOTE, "reason": "direct"} for _ in claims
                ],
            ),
        ):
            return research.research_topic("Single site topic", on_progress=self.events.append)

    def test_zero_verified_run_records_an_unanswered_question_and_saves_nothing(self) -> None:
        archive = self._single_site_run()

        self.assertFalse(archive["memory_updated"])
        self.assertEqual(archive["failure_reason"], "no_independent_support")
        self.assertEqual(archive["outcome_message"], inquiries.USER_MESSAGE)
        self.replace.assert_not_called()
        self.assertIsNone(memory.load_topic("Single site topic"))
        save_events = [event for event in self.events if event["step"] == "save"]
        self.assertEqual(save_events[-1]["status"], "skipped")

        record = inquiries.load_inquiry(archive["inquiry_id"])
        self.assertEqual(record["question"], "Single site topic")
        self.assertEqual(record["outcome"]["message"], inquiries.USER_MESSAGE)
        self.assertIn("3 of the 3 sources came from example.org", record["outcome"]["explanation"])
        self.assertEqual(record["claims"][0]["status_label"], "One source only")
        self.assertEqual(record["summary"]["independent_sites"], 1)
        self.assertEqual(record["process"][-1]["status"], "stopped")
        codes = {finding["code"] for finding in record["developer"]["diagnostics"]}
        self.assertIn("low_origin_diversity", codes)
        self.assertIn("single_origin_support", codes)
        self.assertTrue(record["developer"]["events"])
        self.assertEqual(record["developer"]["claims"][0]["checked_origins"], ["example.org"])

    def test_no_search_results_is_an_unanswered_question_not_an_error(self) -> None:
        with patch.object(research, "search_topic_sources", return_value=[]):
            archive = research.research_topic("Nothing online", on_progress=self.events.append)

        self.assertFalse(archive["memory_updated"])
        self.assertEqual(archive["failure_reason"], "no_search_results")
        record = inquiries.load_inquiry(archive["inquiry_id"])
        self.assertEqual([step["status"] for step in record["process"][:3]], ["done", "stopped", "not_reached"])
        skipped = {event["step"] for event in self.events if event["status"] == "skipped"}
        self.assertEqual(skipped, {"collect", "extract", "verify", "save"})

    def test_unreadable_sources_are_an_unanswered_question(self) -> None:
        hits = [SearchHit("Result", "https://example.org/a", "")]
        pages = [FetchedPage("https://example.org/a", "Result", "text/html", "too short")]
        with (
            patch.object(research, "search_topic_sources", return_value=hits),
            patch.object(research, "collect_research_sources", return_value=(pages, [])),
        ):
            archive = research.research_topic("Unreadable topic")

        self.assertEqual(archive["failure_reason"], "no_readable_sources")
        record = inquiries.load_inquiry(archive["inquiry_id"])
        self.assertEqual(record["developer"]["sources"][0]["read"], "too_little_text")

    def test_storage_failure_does_not_fail_the_research(self) -> None:
        with patch.object(inquiries, "write_json_atomic", side_effect=OSError("disk full")):
            archive = self._single_site_run()
        self.assertFalse(archive["memory_updated"])
        self.assertEqual(archive["inquiry_id"], "")

    def test_research_over_http_returns_the_message_and_a_downloadable_report(self) -> None:
        rate_limit.reset_all()
        client = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000))
        urls = [f"https://docs.example.org/page-{index}" for index in range(2)]
        hits = [SearchHit(f"Page {index}", url, "") for index, url in enumerate(urls)]
        pages = [FetchedPage(hit.url, hit.title, "text/html", SOURCE_TEXT) for hit in hits]
        with (
            patch.object(research, "search_topic_sources", return_value=hits),
            patch.object(research, "collect_research_sources", return_value=(pages, [])),
            patch.object(research, "_extract_from_text", return_value=[{"claim": "One site says so.", "quote": QUOTE}]),
            patch.object(
                research,
                "_compare_claims_with_source",
                side_effect=lambda claims, _s, _t: [{"verdict": "supports", "quote": QUOTE, "reason": ""} for _ in claims],
            ),
        ):
            response = client.post("/api/research", json={"topic": "HTTP topic", "search_queries": ["http topic"]})

        self.assertEqual(response.status_code, 200)
        archive = response.json()["archive"]
        self.assertFalse(archive["memory_updated"])
        self.assertEqual(archive["outcome_message"], inquiries.USER_MESSAGE)
        self.assertIn("example.org", archive["outcome_explanation"])
        record = inquiries.load_inquiry(archive["inquiry_id"])
        self.assertEqual(record["developer"]["plan"]["planner"], "provided")
        self.assertIn("memory", record["developer"]["timings_ms"])
        report = client.get(f"/api/inquiries/{archive['inquiry_id']}/report.pdf")
        self.assertEqual(report.status_code, 200)
        self.assertTrue(report.content.startswith(b"%PDF"))

    def test_reports_render_without_secrets(self) -> None:
        archive = self._single_site_run()
        record = inquiries.load_inquiry(archive["inquiry_id"])

        pdf = reports.render_user_report(record)
        log = reports.render_developer_log(record)

        self.assertTrue(pdf.startswith(b"%PDF"))
        text = "".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)
        self.assertIn("We cannot verify the claims", text)
        self.assertIn("Single site topic", text)
        parsed = json.loads(log)
        self.assertEqual(parsed["report"], "spymonk.developer_log")
        self.assertIn("diagnostics", parsed)
        self.assertNotIn(SECRET.encode(), pdf + log)

    def test_wrapped_lines_after_devanagari_keep_their_font_size(self) -> None:
        if not reports.resolve_fonts()["devanagari"]:
            self.skipTest("No Devanagari font installed")
        record = inquiries.load_inquiry(self._single_site_run()["inquiry_id"])
        record["question"] = "चाणक्य नीति में परिश्रम के बारे में क्या कहा गया है? (Chanakya Neeti on work ethics)"
        pdf = reports.render_user_report(record)
        text = PdfReader(io.BytesIO(pdf)).pages[0].extract_text()
        # A lost font setting draws the second line in tiny, spaced-out glyphs.
        self.assertIn("ethics", text)

    def test_report_renders_without_unicode_fonts(self) -> None:
        record = inquiries.load_inquiry(self._single_site_run()["inquiry_id"])
        record["question"] = "“Curly quotes” and देवनागरी text"
        reports.resolve_fonts.cache_clear()
        self.addCleanup(reports.resolve_fonts.cache_clear)
        with patch.object(reports, "_find", return_value=None), self.assertLogs(reports.logger, "WARNING"):
            pdf = reports.render_user_report(record)
        self.assertTrue(pdf.startswith(b"%PDF"))


class UnansweredQuestionApiTests(unittest.TestCase):
    def setUp(self) -> None:
        rate_limit.reset_all()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        memory_dir = Path(temporary.name) / "memory" / "topics"
        patcher = patch.object(config, "MEMORY_DIR", memory_dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000))
        self.record = {
            "schema_version": 1,
            "id": "20261019120000" + "0" * 16,
            "created_at": "2026-10-19T12:00:00+00:00",
            "question": "Stored question",
            "topic_slug": "stored-question-abc",
            "outcome": {"message": inquiries.USER_MESSAGE, "reason": "no_candidate_claims", "reason_label": "No clear answer"},
            "summary": {"claims_found": 0, "sources_read": 2},
            "process": [],
            "claims": [],
            "sources": [],
            "suggestions": ["Ask a more specific question."],
            "developer": {"diagnostics": []},
        }
        directory = inquiries.inquiries_dir()
        directory.mkdir(parents=True)
        (directory / f"{self.record['id']}.json").write_text(json.dumps(self.record), encoding="utf-8")

    def test_list_and_download_reports(self) -> None:
        listing = self.client.get("/api/inquiries")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["items"][0]["question"], "Stored question")

        pdf = self.client.get(f"/api/inquiries/{self.record['id']}/report.pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf.headers["content-type"], "application/pdf")
        self.assertIn('attachment; filename="spymonk-report-stored-question-abc-', pdf.headers["content-disposition"])
        self.assertTrue(pdf.content.startswith(b"%PDF"))

        log = self.client.get(f"/api/inquiries/{self.record['id']}/developer-log")
        self.assertEqual(log.status_code, 200)
        self.assertEqual(log.json()["inquiry_id"], self.record["id"])

    def test_unknown_and_malformed_ids_are_rejected(self) -> None:
        self.assertEqual(self.client.get(f"/api/inquiries/{'2' * 14}{'f' * 16}/report.pdf").status_code, 404)
        # An encoded slash leaves no matching route; either way nothing is read.
        self.assertIn(self.client.get("/api/inquiries/..%2F..%2Fsecrets/report.pdf").status_code, {404, 422})
        self.assertEqual(self.client.get("/api/inquiries/not-an-id/developer-log").status_code, 422)


if __name__ == "__main__":
    unittest.main()
