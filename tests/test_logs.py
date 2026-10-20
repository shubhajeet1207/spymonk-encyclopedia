"""The API log file and per-run research logs live under the data directory."""

from __future__ import annotations

import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.core import config, run_log
from backend.core.research_trace import ResearchTrace
from backend.observability.logging import disable_file_logging, enable_file_logging, request_id_context


class FileLoggingTests(unittest.TestCase):
    def test_log_lines_reach_the_file_with_the_request_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = enable_file_logging(Path(directory), max_bytes=1024 * 1024, backups=2)
            self.addCleanup(disable_file_logging)
            # A second call must not attach a second handler.
            enable_file_logging(Path(directory), max_bytes=1024 * 1024, backups=2)
            token = request_id_context.set("file-log-test")
            try:
                logging.getLogger("spymonk.test").warning("Stored in the log file")
            finally:
                request_id_context.reset(token)
            disable_file_logging()

            lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len([line for line in lines if "Stored in the log file" in line]), 1)
        self.assertIn("[request_id=file-log-test] spymonk.test", lines[-1])


class ResearchRunLogTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name) / "research"
        self.addCleanup(run_log.disable)
        self.archive = {
            "claims": [{"id": "a", "claim": "Saved claim.", "validation_status": "corroborated", "supporting_origins": ["a.org", "b.org"]}],
            "unresolved_claims": [{"id": "b", "claim": "Single claim.", "validation_status": "single_source", "supporting_origins": ["a.org"]}],
            "held_back_claims": 0,
            "carried_over_claims": 0,
        }

    def _write(self, status: str = "saved") -> Path | None:
        return run_log.write_run_log(
            "How to run a docker container?",
            status=status,
            archive=self.archive,
            trace=ResearchTrace(),
            failure_counts={"source_read": 0, "extraction": 0, "cross_check": 0},
        )

    def test_nothing_is_written_until_the_owning_process_enables_it(self) -> None:
        self.assertIsNone(self._write())
        self.assertFalse(self.directory.exists())

    def test_saved_runs_get_a_developer_log(self) -> None:
        run_log.enable(self.directory)
        path = self._write()

        self.assertRegex(path.name, r"^\d{8}T\d{6}Z-saved-how-to-run-a-docker-container-[0-9a-f]+-[0-9a-f]{6}\.json$")
        log = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(log["report"], "spymonk.research_run")
        self.assertEqual(log["outcome"]["verified_claims"], 1)
        self.assertEqual(len(log["claims"]), 2)
        self.assertIn("diagnostics", log)

    def test_only_the_newest_logs_are_kept(self) -> None:
        run_log.enable(self.directory)
        with patch.object(config, "RESEARCH_LOG_KEEP", 2):
            # Reverse alphabetical order, so name order and age order disagree.
            paths = [self._write(status) for status in ("unanswered", "saved_with_warnings", "saved")]
        remaining = sorted(path.name for path in self.directory.glob("*.json"))
        self.assertEqual(len(remaining), 2)
        self.assertIn(paths[-1].name, remaining)


if __name__ == "__main__":
    unittest.main()
