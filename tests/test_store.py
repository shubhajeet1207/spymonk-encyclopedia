"""Regression tests for persistent vector-store maintenance."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import chromadb

from backend.core import config, store


class ClearVectorStoreTests(unittest.TestCase):
    def test_clear_open_local_collection_without_deleting_database_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index_dir = root / "chroma_db"
            with (
                patch.object(config, "VECTOR_STORE_PROVIDER", "chroma-local"),
                patch.object(config, "CHROMA_DIR", index_dir),
                patch.object(config, "MEMORY_DIR", root / "memory" / "topics"),
                patch.object(config, "PDF_DIR", root / "data" / "pdfs"),
                patch.object(config, "WEB_HTML_DIR", root / "data" / "web_html"),
                patch.object(config, "WEB_PDF_DIR", root / "data" / "web_pdfs"),
            ):
                client = chromadb.PersistentClient(path=str(index_dir))
                try:
                    self.assertFalse(store.index_exists())
                    collection = client.get_or_create_collection(
                        name=config.COLLECTION_NAME, embedding_function=None
                    )
                    collection.add(
                        ids=["existing"],
                        embeddings=[[1.0, 0.0]],
                        documents=["An indexed document"],
                    )
                    self.assertTrue(store.index_exists())

                    store.clear_vectorstore()

                    self.assertTrue(index_dir.exists())
                    replacement = client.get_collection(
                        name=config.COLLECTION_NAME, embedding_function=None
                    )
                    self.assertEqual(replacement.count(), 0)
                    self.assertFalse(store.index_exists())

                    replacement.add(
                        ids=["new"],
                        embeddings=[[1.0, 0.0]],
                        documents=["A new indexed document"],
                    )
                    self.assertEqual(replacement.count(), 1)
                    self.assertTrue(store.index_exists())

                    store.clear_vectorstore()
                    self.assertEqual(
                        client.get_collection(
                            name=config.COLLECTION_NAME, embedding_function=None
                        ).count(),
                        0,
                    )
                    self.assertFalse(store.index_exists())
                finally:
                    # Release SQLite before TemporaryDirectory removes this
                    # throwaway database on Windows.
                    store.close_clients()
                    client.close()


if __name__ == "__main__":
    unittest.main()
