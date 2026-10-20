"""Snapshot metadata stays portable and cannot open arbitrary host files."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.core import config, snapshot_paths, verify


class SnapshotPathTests(unittest.TestCase):
    def test_relative_path_round_trip_and_safe_legacy_absolute_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "data/web_html/page.html"
            snapshot.parent.mkdir(parents=True)
            snapshot.write_text("Safe source", encoding="utf-8")
            with patch.object(config, "DATA_DIR", root):
                encoded = snapshot_paths.metadata_path(snapshot)
                self.assertEqual(encoded, "data/web_html/page.html")
                self.assertEqual(snapshot_paths.resolve_snapshot_path(encoded), snapshot.resolve())
                self.assertEqual(snapshot_paths.resolve_snapshot_path(str(snapshot)), snapshot.resolve())

    def test_outside_and_traversal_paths_are_rejected_before_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "storage"
            root.mkdir()
            outside = Path(directory) / "private.txt"
            outside.write_text("private", encoding="utf-8")
            with patch.object(config, "DATA_DIR", root):
                self.assertIsNone(snapshot_paths.resolve_snapshot_path(str(outside)))
                self.assertIsNone(snapshot_paths.resolve_snapshot_path("../private.txt"))
                with self.assertRaises(ValueError):
                    snapshot_paths.metadata_path(outside)
                with patch.object(verify, "source_text_from_html") as read_source:
                    text = verify.source_text_from_record(
                        {"local_path": str(outside), "source_type": "web_html", "source_url": ""}
                    )
                self.assertEqual(text, "")
                read_source.assert_not_called()


if __name__ == "__main__":
    unittest.main()
