"""Index identity, rebuild, compatibility, and retrieval against a real temporary Chroma."""

from __future__ import annotations

import math
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from backend.core import config, embeddings, evidence, ingest, memory, query, reindex, store


class KeywordEmbeddings(Embeddings):
    """Unit vectors keyed by topic words, so similarity is predictable."""

    AXES = ("docker", "sanskrit", "cooking")

    def _vector(self, text: str) -> list[float]:
        lowered = text.lower()
        values = [1.0 if axis in lowered else 0.0 for axis in self.AXES] + [0.05]
        norm = math.sqrt(sum(value * value for value in values))
        return [value / norm for value in values]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


def _local(text: str, sha: str, index: int = 0) -> Document:
    return Document(
        page_content=text,
        metadata={
            "origin": "local_pdf",
            "source": f"{sha}.pdf",
            "document_sha256": sha,
            "chunk_index": index,
            "chunker": "test",
        },
    )


def _claim(topic: str, claim: str, status: str = "corroborated") -> Document:
    return evidence.claim_document(
        {
            "id": evidence.claim_id(memory.canonical_topic(topic), claim),
            "topic": topic,
            "claim": claim,
            "quote": f"Quoted evidence for {claim}",
            "validation_status": status,
            "supporting_origins": ["first.example", "second.example"],
            "evidence": [],
        }
    )


class IndexLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(config, "DATA_DIR", self.root))
        stack.enter_context(patch.object(config, "VECTOR_STORE_PROVIDER", "chroma-local"))
        for name, relative in (
            ("CHROMA_DIR", "chroma_db"),
            ("MEMORY_DIR", "memory/topics"),
            ("PDF_DIR", "data/pdfs"),
            ("WEB_HTML_DIR", "data/web_html"),
            ("WEB_PDF_DIR", "data/web_pdfs"),
        ):
            stack.enter_context(patch.object(config, name, self.root / relative))
        stack.enter_context(patch.object(embeddings, "get_embeddings", return_value=KeywordEmbeddings()))
        # Runs before the temporary directory is removed (cleanups are LIFO).
        self.addCleanup(store.close_clients)

    def test_rebuild_dedupes_pdfs_skips_image_only_and_restores_answerable_claims(self) -> None:
        config.PDF_DIR.mkdir(parents=True)
        (config.PDF_DIR / "a.pdf").write_bytes(b"%PDF docker guide")
        (config.PDF_DIR / "b.pdf").write_bytes(b"%PDF docker guide")
        (config.PDF_DIR / "scan.pdf").write_bytes(b"%PDF scanned")
        memory.save_topic(
            {
                "topic": "Docker Compose",
                "claims": [
                    {
                        "id": "kept",
                        "claim": "Compose runs docker services.",
                        "quote": "Compose runs docker services together.",
                        "validation_status": "corroborated",
                    }
                ],
                "unresolved_claims": [
                    {"id": "audit-only", "claim": "Unverified docker claim.", "validation_status": "single_source"}
                ],
            }
        )

        def pages(path: Path) -> list[tuple[int, str]]:
            return [(0, "" if path.name == "scan.pdf" else "Docker images package software. " * 20)]

        with patch.object(ingest, "_parse_pdf_isolated", side_effect=pages):
            plan = reindex.plan_reindex()
            result = reindex.run_reindex()

        self.assertEqual(plan.duplicate_pdfs, ["b.pdf"])
        self.assertEqual(result.indexed_pdfs, ["a.pdf"])
        self.assertEqual([item["file"] for item in result.skipped_pdfs], ["scan.pdf"])
        self.assertEqual(result.claims, 1)
        self.assertEqual(store.index_health()["state"], "ok")
        self.assertEqual(store.indexed_pdf_count(), 1)
        self.assertEqual(store.indexed_claim_counts(), {"docker compose": 1})
        self.assertEqual(memory.load_topic("Docker Compose")["index_state"], "indexed")

    def test_failed_rebuild_leaves_live_index_untouched(self) -> None:
        store.upsert_documents([_local("Docker basics", "live")])
        with patch.object(store, "_add_documents", side_effect=RuntimeError("embedding provider down")):
            with self.assertRaises(RuntimeError):
                store.rebuild_collection([_local("Docker replacement", "new")])

        self.assertEqual(store._get_collection().count(), 1)
        self.assertIsNone(store._get_collection(config.COLLECTION_NAME + store.REBUILD_SUFFIX))

    def test_index_built_with_another_embedding_model_is_refused(self) -> None:
        store.upsert_documents([_local("Docker basics", "live")])
        with patch.object(config, "EMBEDDING_MODEL", "another/model"):
            self.assertEqual(store.index_health()["state"], "mismatch")
            with self.assertRaises(store.IndexCompatibilityError):
                store.load_vectorstore()
            with self.assertRaises(store.IndexCompatibilityError):
                store.upsert_documents([_local("Docker more", "other")])

    def test_unfingerprinted_index_is_legacy_until_rebuilt(self) -> None:
        legacy = store._client().create_collection(name=config.COLLECTION_NAME, embedding_function=None)
        legacy.add(ids=["old"], embeddings=[KeywordEmbeddings().embed_query("docker")], documents=["Docker"])
        self.assertEqual(store.index_health()["state"], "legacy")
        self.assertTrue(store.index_health()["rebuild_recommended"])
        store.rebuild_collection([_local("Docker basics", "fresh")])
        self.assertEqual(store.index_health()["state"], "ok")

    def test_refresh_replaces_stale_claims_and_keeps_stable_ids(self) -> None:
        store.replace_topic_web_documents("Docker", [_claim("Docker", "Claim A docker"), _claim("Docker", "Claim B docker")])
        first_ids = set(store._get_collection().get(include=[])["ids"])
        store.replace_topic_web_documents("Docker", [_claim("Docker", "Claim A docker")])
        remaining = set(store._get_collection().get(include=[])["ids"])

        self.assertEqual(len(first_ids), 2)
        self.assertEqual(len(remaining), 1)
        self.assertTrue(remaining <= first_ids)

    def test_exact_topic_lookup_ignores_case_and_spacing(self) -> None:
        store.replace_topic_web_documents("Docker Compose", [_claim("Docker Compose", "Compose docker claim")])
        self.assertEqual(len(store.find_topic_documents("docker   COMPOSE")), 1)

    def test_retrieval_drops_unrelated_duplicate_and_unverified_passages(self) -> None:
        store.upsert_documents(
            [
                _local("Docker images package software.", "first"),
                _local("Docker images package software.", "second"),
                _local("Sanskrit grammar notes.", "third"),
                _claim("Docker", "Docker unverified claim", status="single_source"),
                _claim("Docker", "Docker corroborated claim"),
            ]
        )
        with patch.object(config, "RETRIEVE_MIN_SIMILARITY", 0.25):
            docker = query.retrieve_eligible_documents("docker")
            cooking = query.retrieve_eligible_documents("cooking")

        texts = [document.page_content for document in docker]
        self.assertEqual(texts.count("Docker images package software."), 1)
        self.assertTrue(any("Docker corroborated claim" in text for text in texts))
        self.assertFalse(any("unverified" in text for text in texts))
        self.assertFalse(any("Sanskrit" in text for text in texts))
        self.assertEqual(cooking, [])


if __name__ == "__main__":
    unittest.main()
