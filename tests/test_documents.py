"""Regression tests for PDF upload staging and honest library counts."""

from __future__ import annotations

import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from langchain_core.documents import Document

from backend.core import config, ingest, store
from backend.api.routes import documents
from backend.main import app
from backend.services import rate_limit


class DocumentUploadTests(unittest.TestCase):
    def setUp(self) -> None:
        rate_limit.reset_all()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(config, "DATA_DIR", self.root))
        # Runs first (LIFO): release cached clients before the temp dir is removed.
        self.addCleanup(store.close_clients)
        for name, relative in (
            ("CHROMA_DIR", "chroma_db"),
            ("MEMORY_DIR", "memory/topics"),
            ("PDF_DIR", "data/pdfs"),
            ("WEB_HTML_DIR", "data/web_html"),
            ("WEB_PDF_DIR", "data/web_pdfs"),
        ):
            self.stack.enter_context(patch.object(config, name, self.root / relative))
        self.client = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000))

    def _upload(self, *names: str, same_bytes: bool = False):
        # Distinct bytes per file unless a test is about duplicate content.
        return self.client.post(
            "/api/documents",
            files=[
                ("files", (name, b"%PDF-test" if same_bytes else b"%PDF-test " + name.encode(), "application/pdf"))
                for name in names
            ],
        )

    def _all_pdf_files(self) -> list[Path]:
        directory = config.PDF_DIR
        return list(directory.rglob("*.pdf")) if directory.exists() else []

    def test_image_only_pdf_leaves_no_file_or_stage_directory(self) -> None:
        with patch.object(ingest, "_parse_pdf_isolated", return_value=[(0, "")]):
            response = self._upload("scan.pdf")

        self.assertEqual(response.status_code, 422)
        self.assertIn("from scan.pdf.", response.json()["detail"])
        self.assertEqual(self._all_pdf_files(), [])
        self.assertEqual(list(config.PDF_DIR.iterdir()), [])

    def test_one_image_only_file_rejects_whole_batch_without_orphans(self) -> None:
        def pages(path: Path):
            text = "Searchable content. " * 50 if "good" in path.name else ""
            return [(0, text)]

        with patch.object(ingest, "_parse_pdf_isolated", side_effect=pages), patch.object(ingest, "upsert_documents") as upsert:
            response = self._upload("good.pdf", "scan.pdf")

        self.assertEqual(response.status_code, 422)
        upsert.assert_not_called()
        self.assertEqual(self._all_pdf_files(), [])
        self.assertEqual(list(config.PDF_DIR.iterdir()), [])

    def test_later_invalid_extension_does_not_leave_first_file(self) -> None:
        response = self._upload("first.pdf", "not-a-pdf.txt")

        self.assertEqual(response.status_code, 415)
        self.assertEqual(self._all_pdf_files(), [])
        self.assertEqual(list(config.PDF_DIR.iterdir()), [])

    def test_promotion_failure_cleans_earlier_promoted_file(self) -> None:
        real_link = documents.os.link
        calls = 0

        def fail_second_link(source: Path, target: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated promotion failure")
            real_link(source, target)

        with (
            patch.object(ingest, "_parse_pdf_isolated", return_value=[(0, "Searchable content. " * 50)]),
            patch.object(ingest, "upsert_documents") as upsert,
            patch.object(documents.os, "link", side_effect=fail_second_link),
        ):
            response = self._upload("first.pdf", "second.pdf")

        self.assertEqual(response.status_code, 500)
        upsert.assert_not_called()
        self.assertEqual(self._all_pdf_files(), [])
        self.assertEqual(list(config.PDF_DIR.iterdir()), [])

    def test_index_failure_removes_only_newly_promoted_files(self) -> None:
        config.PDF_DIR.mkdir(parents=True)
        existing = config.PDF_DIR / "existing.pdf"
        existing.write_bytes(b"previous user file")
        with patch.object(ingest, "_parse_pdf_isolated", return_value=[(0, "Searchable content. " * 50)]), patch.object(
            ingest, "upsert_documents", side_effect=RuntimeError("provider unavailable")
        ):
            response = self._upload("new.pdf")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(self._all_pdf_files(), [existing])
        self.assertEqual(existing.read_bytes(), b"previous user file")

    def test_successful_upload_indexes_final_path_not_temporary_path(self) -> None:
        indexed_chunks: list[Document] = []

        def capture(chunks: list[Document], *, reset: bool = False) -> int:
            indexed_chunks.extend(chunks)
            return len(chunks)

        with (
            patch.object(ingest, "_parse_pdf_isolated", return_value=[(0, "Searchable content. " * 50)]),
            patch.object(ingest, "upsert_documents", side_effect=capture),
            patch.object(store, "index_exists", return_value=True),
        ):
            response = self._upload("good.pdf")

        self.assertEqual(response.status_code, 200)
        files = self._all_pdf_files()
        self.assertEqual(len(files), 1)
        self.assertTrue(indexed_chunks)
        self.assertEqual(response.json()["chunk_count"], len(indexed_chunks))
        relative = files[0].resolve().relative_to(self.root.resolve()).as_posix()
        for chunk in indexed_chunks:
            # Relative to SPYMONK_DATA_DIR: the index never records host paths.
            self.assertEqual(chunk.metadata["source_path"], relative)
            self.assertEqual(chunk.metadata["local_path"], relative)
            self.assertEqual(chunk.metadata["source"], files[0].name)
            self.assertEqual(len(chunk.metadata["document_sha256"]), 64)

    def test_identical_files_in_one_batch_are_indexed_once(self) -> None:
        indexed_chunks: list[Document] = []

        def capture(chunks: list[Document], *, reset: bool = False) -> int:
            indexed_chunks.extend(chunks)
            return len(chunks)

        with (
            patch.object(ingest, "_parse_pdf_isolated", return_value=[(0, "Searchable content. " * 50)]),
            patch.object(ingest, "upsert_documents", side_effect=capture),
        ):
            response = self._upload("copy-one.pdf", "copy-two.pdf", same_bytes=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["files"], ["copy-one.pdf"])
        self.assertEqual(response.json()["duplicates"], ["copy-two.pdf"])
        self.assertEqual(len(self._all_pdf_files()), 1)
        self.assertEqual({chunk.metadata["source"].split("-", 1)[1] for chunk in indexed_chunks}, {"copy-one.pdf"})

    def test_file_already_in_index_is_not_stored_or_indexed_again(self) -> None:
        import hashlib

        digest = hashlib.sha256(b"%PDF-test").hexdigest()
        with (
            patch.object(store, "indexed_document_hashes", return_value={digest}),
            patch.object(store, "index_exists", return_value=True),
            patch.object(ingest, "upsert_documents") as upsert,
        ):
            response = self._upload("again.pdf", same_bytes=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["file_count"], 0)
        self.assertEqual(response.json()["duplicates"], ["again.pdf"])
        upsert.assert_not_called()
        self.assertEqual(self._all_pdf_files(), [])

    def test_status_distinguishes_stored_and_indexed_pdfs(self) -> None:
        config.PDF_DIR.mkdir(parents=True)
        (config.PDF_DIR / "indexed.pdf").write_bytes(b"one")
        (config.PDF_DIR / "older-unindexed.pdf").write_bytes(b"two")

        with (
            patch.object(store, "index_exists", return_value=True),
            patch.object(store, "indexed_pdf_count", return_value=1, create=True),
        ):
            response = self.client.get("/api/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["local_pdf_count"], 2)
        self.assertEqual(response.json()["indexed_pdf_count"], 1)

    def test_indexed_pdf_count_deduplicates_chunks_across_pages(self) -> None:
        config.CHROMA_DIR.mkdir(parents=True)
        (config.CHROMA_DIR / "marker").touch()
        first_page = {
            "ids": [str(index) for index in range(500)],
            "metadatas": [{"origin": "local_pdf", "source_path": "first.pdf"}] * 500,
        }
        second_page = {
            "ids": ["500", "501"],
            "metadatas": [
                {"origin": "local_pdf", "source_path": "first.pdf"},
                {"origin": "local_pdf", "source_path": "second.pdf"},
            ],
        }
        with (
            patch.object(config, "VECTOR_STORE_PROVIDER", "chroma-local"),
            patch("chromadb.PersistentClient") as client_factory,
        ):
            client = client_factory.return_value
            collection = client.get_collection.return_value
            collection.get.side_effect = [first_page, second_page]
            count = store.indexed_pdf_count()

        self.assertEqual(count, 2)
        self.assertEqual(collection.get.call_count, 2)
        self.assertEqual(collection.get.call_args_list[0].kwargs["where"], {"origin": "local_pdf"})


if __name__ == "__main__":
    unittest.main()
