"""A failed cross-check holds back only the claims it affected; nothing verified is lost."""

from __future__ import annotations

import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from backend.core import config, memory, research
from backend.core.llm_json import LLMOutputError
from backend.core.scraper import FetchedPage, SearchHit

QUOTE = "This supported sentence is long enough to satisfy the verbatim quote matching rule easily."
SOURCE_TEXT = f"{'Context words. ' * 15}{QUOTE} {'More context. ' * 15}"
SUPPORT = {"verdict": "supports", "quote": QUOTE, "reason": "direct"}


class PartialSaveTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in (
            ("LLM_API_KEY", "test-key"),
            ("DATA_DIR", self.root),
            ("MEMORY_DIR", self.root / "memory" / "topics"),
            ("CROSS_CHECK_BATCH_SIZE", 1),
        ):
            self.stack.enter_context(patch.object(config, name, value))
        # Three independent registrable domains, so two supports corroborate.
        hits = [SearchHit(str(i), f"https://site{i}.org/page", "") for i in range(3)]
        pages = [FetchedPage(hit.url, hit.title, "text/html", SOURCE_TEXT) for hit in hits]
        extracted = [
            {"claim": "Claim A is stated.", "quote": QUOTE, "page": None},
            {"claim": "Claim B is stated.", "quote": QUOTE, "page": None},
        ]
        self.stack.enter_context(patch.object(research, "search_topic_sources", return_value=hits))
        self.stack.enter_context(patch.object(research, "collect_research_sources", return_value=(pages, [])))
        self.stack.enter_context(patch.object(research, "download_reference_pdfs", return_value=[]))
        self.stack.enter_context(patch.object(research, "find_topic_documents", return_value=[]))
        self.stack.enter_context(
            patch.object(research, "_extract_from_text", side_effect=lambda *_a, **_k: list(extracted))
        )
        self.replace = self.stack.enter_context(patch.object(research, "replace_topic_web_documents"))

    def _run(self, compare) -> dict:
        with patch.object(research, "_compare_claims_with_source", side_effect=compare):
            return research.research_topic("Partial topic")

    def test_one_failed_check_holds_back_only_the_claim_it_affected(self) -> None:
        def compare(claims, source, _text):
            if claims == ["Claim B is stated."] and source.url == "https://site2.org/page":
                raise RuntimeError("provider timed out")
            return [SUPPORT for _ in claims]

        archive = self._run(compare)

        self.assertTrue(archive["memory_updated"])
        self.assertEqual([claim["claim"] for claim in archive["claims"]], ["Claim A is stated."])
        held = [claim for claim in archive["unresolved_claims"] if claim["validation_status"] == "incomplete"]
        self.assertEqual([claim["claim"] for claim in held], ["Claim B is stated."])
        self.assertEqual(archive["held_back_claims"], 1)
        self.assertFalse(self.replace.call_args.kwargs["remove_stale"])
        self.assertEqual(memory.load_topic("Partial topic")["index_state"], "indexed")

    def test_a_malformed_reply_is_retried_and_not_counted_as_a_failure(self) -> None:
        attempts: dict[tuple, int] = {}

        def compare(claims, source, _text):
            key = (tuple(claims), source.url)
            attempts[key] = attempts.get(key, 0) + 1
            if attempts[key] == 1 and source.url == "https://site1.org/page":
                raise LLMOutputError("Model output contained invalid JSON")
            return [SUPPORT for _ in claims]

        archive = self._run(compare)

        self.assertEqual(len(archive["claims"]), 2)
        self.assertEqual(archive["held_back_claims"], 0)
        self.assertTrue(self.replace.call_args.kwargs["remove_stale"])

    def test_a_malformed_batch_reply_is_rechecked_one_claim_at_a_time(self) -> None:
        # Resending an identical batch prompt tends to reproduce the same broken
        # reply; smaller single-claim prompts are what actually recover.
        calls: list[tuple[int, str]] = []

        def compare(claims, source, _text):
            calls.append((len(claims), source.url))
            if len(claims) > 1 and source.url == "https://site2.org/page":
                raise LLMOutputError("Model output contained invalid JSON")
            return [SUPPORT for _ in claims]

        with patch.object(config, "CROSS_CHECK_BATCH_SIZE", 6):
            archive = self._run(compare)

        self.assertEqual(len(archive["claims"]), 2)
        self.assertEqual(archive["held_back_claims"], 0)
        self.assertIn((1, "https://site2.org/page"), calls)
        self.assertTrue(self.replace.call_args.kwargs["remove_stale"])

    def test_only_the_claim_whose_own_check_fails_is_held_back(self) -> None:
        def compare(claims, source, _text):
            if source.url == "https://site2.org/page" and (len(claims) > 1 or claims == ["Claim B is stated."]):
                raise LLMOutputError("Model output contained invalid JSON")
            return [SUPPORT for _ in claims]

        with patch.object(config, "CROSS_CHECK_BATCH_SIZE", 6):
            archive = self._run(compare)

        self.assertEqual([claim["claim"] for claim in archive["claims"]], ["Claim A is stated."])
        self.assertEqual(archive["held_back_claims"], 1)
        self.assertEqual(archive["run_warnings"]["failure_counts"]["cross_check"], 1)

    def test_a_degraded_run_keeps_previously_verified_claims(self) -> None:
        memory.save_topic(
            {
                "topic": "Partial topic",
                "claims": [
                    {
                        "id": "earlier-claim",
                        "topic": "Partial topic",
                        "claim": "An earlier verified claim.",
                        "quote": QUOTE,
                        "validation_status": "corroborated",
                    }
                ],
            }
        )

        def compare(claims, source, _text):
            if claims == ["Claim B is stated."] and source.url == "https://site2.org/page":
                raise RuntimeError("provider timed out")
            return [SUPPORT for _ in claims]

        archive = self._run(compare)

        saved_ids = [claim["id"] for claim in archive["claims"]]
        self.assertIn("earlier-claim", saved_ids)
        self.assertEqual(archive["carried_over_claims"], 1)
        indexed_claim_ids = {document.metadata["claim_id"] for document in self.replace.call_args.args[1]}
        self.assertIn("earlier-claim", indexed_claim_ids)
        self.assertEqual(
            [claim["id"] for claim in memory.load_topic("Partial topic")["claims"]],
            saved_ids,
        )


if __name__ == "__main__":
    unittest.main()
