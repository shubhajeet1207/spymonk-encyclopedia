"""Regression tests for fail-closed retrieval and response evidence handling."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.documents import Document

from backend.core import query, verify


class EvidenceGuardTests(unittest.TestCase):
    def test_missing_origin_is_not_trusted_as_local_pdf(self) -> None:
        documents = [
            Document(page_content="No origin", metadata={}),
            Document(page_content="Claimed status", metadata={"validation_status": "corroborated"}),
            Document(page_content="Trusted PDF", metadata={"origin": "local_pdf"}),
        ]
        with patch.object(query, "retrieve", return_value=documents):
            eligible = query.retrieve_eligible_documents("question")

        self.assertEqual([doc.page_content for doc in eligible], ["Trusted PDF"])

    def test_missing_origin_fails_quote_reverification(self) -> None:
        document = Document(page_content="Untrusted", metadata={"validation_status": "corroborated"})
        self.assertFalse(verify.document_still_verified(document))

    def test_non_list_evidence_is_excluded_from_api_sources(self) -> None:
        document = Document(
            page_content="Verified claim",
            metadata={"origin": "web", "validation_status": "corroborated", "evidence_json": "{}"},
        )
        model = SimpleNamespace(invoke=lambda _messages: SimpleNamespace(content="Supported answer"))
        with patch.object(query, "get_llm", return_value=model):
            answer = query.answer_from_documents("question", [document])

        self.assertEqual(answer.sources[0]["evidence"], [])


if __name__ == "__main__":
    unittest.main()
