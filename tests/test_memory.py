"""Regression tests for collision-safe and durable topic audit files."""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from backend.core import memory


class TopicMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, relative in (
            ("CHROMA_DIR", "chroma_db"),
            ("MEMORY_DIR", "memory/topics"),
            ("PDF_DIR", "data/pdfs"),
            ("WEB_HTML_DIR", "data/web_html"),
            ("WEB_PDF_DIR", "data/web_pdfs"),
        ):
            self.stack.enter_context(
                patch.object(memory.config, name, self.root / relative)
            )

    def _legacy_file(self, filename: str, topic: str, *, claim_id: str = "old") -> Path:
        directory = memory.config.MEMORY_DIR
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / filename
        path.write_text(
            json.dumps({"topic": topic, "slug": path.stem, "claims": [{"id": claim_id}]}),
            encoding="utf-8",
        )
        return path

    def test_unicode_punctuation_and_long_topics_have_distinct_filenames(self) -> None:
        topics = [
            "भगवद् गीता",
            "महाभारत",
            "C",
            "C++",
            "A" * 100 + " first",
            "A" * 100 + " second",
        ]
        paths = [memory.save_topic({"topic": topic, "claims": []}) for topic in topics]

        self.assertEqual(len(set(paths)), len(topics))
        self.assertIn("भगवद्", paths[0].name)
        self.assertEqual({item["topic"] for item in memory.list_topics()}, set(topics))
        for topic, path in zip(topics, paths):
            self.assertEqual(memory.load_topic(topic)["topic"], topic)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["slug"], path.stem)

    def test_canonical_topic_stabilizes_case_width_and_whitespace(self) -> None:
        self.assertEqual(memory.topic_slug("  Ｄｏｃｋｅｒ   COMPOSE "), memory.topic_slug("docker compose"))
        self.assertNotEqual(memory.topic_slug("C++"), memory.topic_slug("C"))

    def test_legacy_lookup_checks_topic_before_returning_colliding_file(self) -> None:
        self._legacy_file("topic.json", "भगवद् गीता")

        self.assertEqual(memory.load_topic("भगवद् गीता")["topic"], "भगवद् गीता")
        self.assertIsNone(memory.load_topic("महाभारत"))

    def test_migration_is_dry_run_by_default_and_never_clobbers(self) -> None:
        first = self._legacy_file("topic.json", "भगवद् गीता")
        second = self._legacy_file("c.json", "C++")
        original_first = first.read_bytes()

        preview = memory.migrate_topic_files(memory.config.MEMORY_DIR)
        self.assertEqual([result.status for result in preview], ["ready", "ready"])
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())
        self.assertEqual(first.read_bytes(), original_first)

        applied = memory.migrate_topic_files(memory.config.MEMORY_DIR, apply=True)
        self.assertEqual([result.status for result in applied], ["migrated", "migrated"])
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())
        for result in applied:
            self.assertTrue(result.destination.exists())
            migrated = json.loads(result.destination.read_text(encoding="utf-8"))
            self.assertEqual(migrated["slug"], result.destination.stem)

        legacy = self._legacy_file("topic.json", "भगवद् गीता", claim_id="newer")
        current = memory.config.MEMORY_DIR / f"{memory.topic_slug('भगवद् गीता')}.json"
        current_bytes = current.read_bytes()
        conflict = memory.migrate_topic_files(memory.config.MEMORY_DIR, apply=True)
        self.assertEqual(next(item.status for item in conflict if item.source == legacy), "conflict")
        self.assertTrue(legacy.exists())
        self.assertEqual(current.read_bytes(), current_bytes)

    def test_malformed_archives_are_skipped_without_exposing_contents_in_logs(self) -> None:
        directory = memory.config.MEMORY_DIR
        directory.mkdir(parents=True)
        (directory / "list.json").write_text('[{"secret": "private"}]', encoding="utf-8")
        (directory / "null.json").write_text('{"topic": "Bad", "claims": null}', encoding="utf-8")
        (directory / "blank.json").write_text('{"topic": "   ", "claims": []}', encoding="utf-8")
        (directory / "encoding.json").write_bytes(b"\xffsecret")
        (directory / "truncated.json").write_text('{"topic": "Bad", "claims":', encoding="utf-8")
        good = memory.save_topic({"topic": "Good", "claims": [{"id": "claim-1"}]})

        with self.assertLogs(memory.logger, level="WARNING") as captured:
            topics = memory.list_topics()
            archives = list(memory.iter_topic_archives())

        self.assertEqual([item["topic"] for item in topics], ["Good"])
        self.assertEqual(topics[0]["path"], str(good))
        self.assertGreaterEqual(len(captured.output), 5)
        self.assertNotIn("secret", " ".join(captured.output))
        self.assertEqual([archive["claims"] for archive in archives], [[{"id": "claim-1"}]])

    def test_migration_preserves_invalid_and_unexpected_files(self) -> None:
        directory = memory.config.MEMORY_DIR
        directory.mkdir(parents=True)
        invalid = directory / "broken.json"
        invalid.write_text('{"topic":', encoding="utf-8")
        unexpected = self._legacy_file("hand-renamed.json", "Different topic")
        previous = {path: path.read_bytes() for path in (invalid, unexpected)}

        with self.assertLogs(memory.logger, level="WARNING"):
            results = memory.migrate_topic_files(directory, apply=True)

        self.assertEqual({item.status for item in results}, {"invalid", "unexpected_name"})
        for path, content in previous.items():
            self.assertEqual(path.read_bytes(), content)

    def test_failed_atomic_replace_preserves_previous_file(self) -> None:
        archive = {"topic": "Durable", "claims": [{"id": "first"}]}
        with patch.object(memory.os, "fsync", wraps=memory.os.fsync) as sync:
            path = memory.save_topic(archive)
        sync.assert_called_once()
        original = path.read_bytes()
        replacement = {"topic": "Durable", "claims": [{"id": "second"}]}

        with patch.object(memory.os, "replace", side_effect=OSError("sensitive failure")):
            with self.assertRaises(OSError):
                memory.save_topic(replacement)

        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(path.parent.glob(".*.tmp")), [])
        self.assertNotIn("updated_at", replacement)

    def test_invalid_replacement_is_rejected_before_touching_previous_file(self) -> None:
        path = memory.save_topic({"topic": "Durable", "claims": []})
        original = path.read_bytes()

        with self.assertRaises(ValidationError):
            memory.save_topic({"topic": "Durable", "claims": None})

        self.assertEqual(path.read_bytes(), original)

    def test_hash_collision_or_unreadable_target_never_overwrites_audit(self) -> None:
        with patch.object(memory, "topic_slug", return_value="forced-collision"):
            path = memory.save_topic({"topic": "First", "claims": [{"id": "one"}]})
            original = path.read_bytes()
            with self.assertRaises(ValueError):
                memory.save_topic({"topic": "Second", "claims": [{"id": "two"}]})
            self.assertEqual(path.read_bytes(), original)

            path.write_text('{"topic":', encoding="utf-8")
            with self.assertLogs(memory.logger, level="WARNING"):
                with self.assertRaises(ValueError):
                    memory.save_topic({"topic": "First", "claims": []})
            self.assertEqual(path.read_text(encoding="utf-8"), '{"topic":')

    def test_successful_refresh_keeps_previous_archive_in_history(self) -> None:
        path = memory.save_topic({"topic": "Historical", "claims": [{"id": "old"}]})
        previous = json.loads(path.read_text(encoding="utf-8"))

        memory.save_topic({"topic": "Historical", "claims": [{"id": "new"}]})

        self.assertEqual(memory.load_topic("Historical")["claims"], [{"id": "new"}])
        history = list((self.root / "memory/history").glob("*.json"))
        self.assertEqual(len(history), 1)
        self.assertEqual(json.loads(history[0].read_text(encoding="utf-8")), previous)

    def test_failed_history_write_leaves_current_archive_untouched(self) -> None:
        path = memory.save_topic({"topic": "Historical", "claims": [{"id": "old"}]})
        previous = path.read_bytes()
        with patch.object(memory, "write_json_atomic", side_effect=OSError("test")):
            with self.assertRaises(OSError):
                memory.save_topic({"topic": "Historical", "claims": [{"id": "new"}]})
        self.assertEqual(path.read_bytes(), previous)


if __name__ == "__main__":
    unittest.main()
