"""PERF-04, DATA-08/09/12, ARCH-07: snapshot text cache, chunk identity and size, instance lock."""

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from langchain_core.documents import Document

from backend.core import config, ingest, snapshot_paths, verify
from backend.core.instance_lock import InstanceLock, InstanceLockError


class SnapshotTextTests(unittest.TestCase):
    def setUp(self) -> None:
        snapshot_paths.clear_snapshot_caches()
        self.addCleanup(snapshot_paths.clear_snapshot_caches)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_text_is_extracted_once_then_served_from_cache_or_sidecar(self) -> None:
        snapshot = self.root / "page-abc.html"
        snapshot.write_bytes(b"<p>hello</p>")
        extract = MagicMock(return_value="hello")

        self.assertEqual(snapshot_paths.snapshot_text(snapshot, extract), "hello")
        self.assertEqual(snapshot_paths.snapshot_text(snapshot, extract), "hello")
        snapshot_paths.clear_snapshot_caches()
        self.assertEqual(snapshot_paths.snapshot_text(snapshot, extract), "hello")

        extract.assert_called_once()
        self.assertTrue(snapshot_paths.sidecar_path(snapshot).exists())

    def test_snapshot_that_no_longer_matches_its_hash_is_not_trusted(self) -> None:
        snapshot = self.root / "data" / "web_html" / "page.html"
        snapshot.parent.mkdir(parents=True)
        snapshot.write_bytes(b"<p>tampered</p>")
        record = {
            "local_path": "data/web_html/page.html",
            "source_type": "web_html",
            "source_url": "https://site.org/page",
            "snapshot_sha256": hashlib.sha256(b"<p>original</p>").hexdigest(),
        }
        with patch.object(config, "DATA_DIR", self.root), patch.object(verify, "source_text_from_html") as read:
            self.assertEqual(verify.source_text_from_record(record), "")
        read.assert_not_called()


class ChunkingTests(unittest.TestCase):
    def test_chunks_are_numbered_per_document_and_repeats_are_dropped(self) -> None:
        repeated = "Docker images package an application with its dependencies."
        pages = [
            Document(page_content=repeated, metadata={"document_sha256": "a", "page": 0}),
            Document(page_content=repeated, metadata={"document_sha256": "a", "page": 1}),
            Document(page_content="A different page.", metadata={"document_sha256": "a", "page": 2}),
            Document(page_content=repeated, metadata={"document_sha256": "b", "page": 0}),
        ]
        with patch.object(config, "EMBEDDING_PROVIDER", "openai"):
            ingest.get_splitter.cache_clear()
            self.addCleanup(ingest.get_splitter.cache_clear)
            chunks = ingest.chunk_documents(pages)

        numbered = [(chunk.metadata["document_sha256"], chunk.metadata["chunk_index"]) for chunk in chunks]
        self.assertEqual(numbered, [("a", 0), ("a", 1), ("b", 0)])
        self.assertTrue(all(chunk.metadata["chunker"].startswith("tiktoken:") for chunk in chunks))

    def test_local_chunks_fit_the_embedding_model_input(self) -> None:
        try:
            from transformers import AutoTokenizer

            tokenizer = AutoTokenizer.from_pretrained(config.EMBEDDING_MODEL, local_files_only=True)
        except Exception:
            self.skipTest("Local embedding tokenizer is not cached")
        text = " ".join(f"Sentence {index} explains how container images and volumes work." for index in range(200))
        with patch.object(config, "EMBEDDING_PROVIDER", "huggingface"):
            ingest.get_splitter.cache_clear()
            self.addCleanup(ingest.get_splitter.cache_clear)
            chunks = ingest.chunk_documents([Document(page_content=text, metadata={"document_sha256": "a"})])

        self.assertGreater(len(chunks), 1)
        longest = max(len(tokenizer(chunk.page_content)["input_ids"]) for chunk in chunks)
        self.assertLessEqual(longest, config.EMBEDDING_MAX_TOKENS)


class InstanceLockTests(unittest.TestCase):
    def test_second_process_on_the_same_data_dir_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".lock"
            first = InstanceLock(path)
            first.acquire()
            try:
                with self.assertRaises(InstanceLockError):
                    InstanceLock(path).acquire()
            finally:
                first.release()
            again = InstanceLock(path)
            again.acquire()
            again.release()


class ConfigValidationTests(unittest.TestCase):
    def test_invalid_values_name_the_variable(self) -> None:
        with patch.dict(os.environ, {"LLM_MAX_TOKENS": "lots"}):
            with self.assertRaisesRegex(ValueError, "LLM_MAX_TOKENS"):
                config._env_int("LLM_MAX_TOKENS", 1)
        with patch.dict(os.environ, {"VECTOR_STORE_PROVIDER": "pinecone"}):
            with self.assertRaisesRegex(ValueError, "VECTOR_STORE_PROVIDER"):
                config._env_choice("VECTOR_STORE_PROVIDER", "chroma-local", {"chroma-local", "chroma-cloud"})
        with patch.dict(os.environ, {"FLAG": "maybe"}):
            with self.assertRaisesRegex(ValueError, "FLAG"):
                config._env_bool("FLAG", False)


if __name__ == "__main__":
    unittest.main()
