"""A research run that saves verified claims also answers the question from them."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from langchain_core.documents import Document

from backend.core import query, research, store
from backend.core.query import EncyclopediaAnswer
from backend.core.research import ResearchCancelled
from backend.services import archive

SAVED = {
    "topic": "how to run a docker container?",
    "memory_updated": True,
    "claims": [{"id": "c1", "claim": "docker run starts a container.", "supporting_origins": ["a.org", "b.org"]}],
    "unresolved_claims": [],
    "sources": [],
}
UNANSWERED = {**SAVED, "memory_updated": False, "claims": [], "inquiry_id": "20261020000000" + "0" * 16}


class AnswerAfterResearchTests(unittest.TestCase):
    def _run(self, saved_archive: dict, *, answer=None, answer_error=None, cancel_at_answer=False) -> tuple[dict, list[dict], object]:
        events: list[dict] = []

        def check() -> None:
            if cancel_at_answer and any(event["step"] == "answer" for event in events):
                raise ResearchCancelled("cancelled")

        with (
            patch.object(research, "research_topic", side_effect=lambda *_a, **_k: dict(saved_archive)),
            patch.object(store, "find_topic_documents", return_value=[Document(page_content="docker run starts a container.")]) as find,
            patch.object(
                query,
                "answer_from_documents",
                side_effect=answer_error,
                return_value=answer or EncyclopediaAnswer(answer="Use docker run IMAGE.", sources=[{"source": "a.org"}]),
            ),
        ):
            response = archive.lookup_or_research(
                saved_archive["topic"],
                refresh=True,
                search_queries=["docker run"],
                on_progress=events.append,
                check_cancelled=check,
            )
        return response, [event for event in events if event["step"] == "answer"], find

    def test_saved_claims_produce_an_answer(self) -> None:
        response, events, _ = self._run(SAVED)
        self.assertEqual(response["answer"], "Use docker run IMAGE.")
        self.assertEqual(response["sources"], [{"source": "a.org"}])
        self.assertEqual([event["status"] for event in events], ["active", "complete"])

    def test_an_answer_failure_keeps_the_saved_research(self) -> None:
        response, events, _ = self._run(SAVED, answer_error=TimeoutError("model timed out"))
        self.assertEqual(response["answer"], "")
        self.assertTrue(response["archive"]["memory_updated"])
        self.assertEqual(events[-1]["status"], "skipped")
        self.assertIn("Ask the question in the chat", events[-1]["message"])

    def test_cancelling_after_the_save_skips_only_the_answer(self) -> None:
        response, events, _ = self._run(SAVED, cancel_at_answer=True)
        self.assertEqual(response["answer"], "")
        self.assertEqual(events[-1]["status"], "skipped")

    def test_unanswered_runs_skip_the_answer_without_searching(self) -> None:
        response, events, find = self._run(UNANSWERED)
        self.assertEqual(response["answer"], "")
        self.assertEqual([event["status"] for event in events], ["skipped"])
        find.assert_not_called()

    def test_claims_that_do_not_answer_leave_the_claims_view(self) -> None:
        response, events, _ = self._run(SAVED, answer=EncyclopediaAnswer(answer=query.NOT_FOUND, sources=[]))
        self.assertEqual(response["answer"], "")
        self.assertEqual(events[-1]["status"], "complete")


if __name__ == "__main__":
    unittest.main()
