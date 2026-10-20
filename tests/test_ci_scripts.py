"""The CI gates themselves: they must catch what they claim to catch."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

import check_contracts  # noqa: E402
import check_docs_links  # noqa: E402
import check_repo_policy  # noqa: E402


class RepoPolicyTests(unittest.TestCase):
    def test_personal_data_and_secrets_are_rejected(self) -> None:
        problems = check_repo_policy.violations(
            [
                ".env",
                "backend/.env",
                ".env.production",
                "data/pdfs/paper.pdf",
                "memory/topics/topic.json",
                "logs/spymonk-api.log",
                "chroma_db/chroma.sqlite3",
                "deploy/server.pem",
                "id_ed25519",
            ]
        )
        self.assertEqual(len(problems), 9)

    def test_placeholders_and_code_are_allowed(self) -> None:
        self.assertEqual(
            check_repo_policy.violations([".env.example", "frontend/.env.example", "data/pdfs/.gitkeep", "memory/topics/.gitkeep"]),
            [],
        )

    def test_large_files_and_conflict_markers_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "big.bin").write_bytes(b"\0" * (check_repo_policy.MAX_BYTES + 1))
            (root / "merge.py").write_text("x = 1\n<<<<<<< HEAD\ny = 2\n", encoding="utf-8")
            (root / "fine.md").write_text("A heading\n=======\n", encoding="utf-8")
            with patch.object(check_repo_policy, "ROOT", root):
                problems = check_repo_policy.violations(["big.bin", "merge.py", "fine.md"])
        self.assertEqual([problem.split(":")[0] for problem in problems], ["big.bin", "merge.py"])


class DocsLinkTests(unittest.TestCase):
    def test_broken_relative_links_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            (root / "docs" / "real.md").write_text("ok", encoding="utf-8")
            page = root / "docs" / "index.md"
            page.write_text(
                "[good](real.md) [anchor](real.md#part) [web](https://example.org) [here](#top)\n"
                "[missing](gone.md) ![image](../img/none.png)\n"
                "```\n[inside a code block](ignored.md)\n```\n",
                encoding="utf-8",
            )
            with patch.object(check_docs_links, "ROOT", root):
                problems = check_docs_links.broken_links(page)
        self.assertEqual([problem.rsplit(": ", 1)[1] for problem in problems], ["gone.md", "../img/none.png"])


class ContractTests(unittest.TestCase):
    def test_shapes_keep_keys_and_types_but_not_values(self) -> None:
        shape = check_contracts.shape({"id": "x", "count": 3, "nested": {"deep": {"deeper": 1}}, "items": [{"a": None}], "empty": []})
        self.assertEqual(
            shape,
            {"count": "int", "empty": "array", "id": "str", "items": ["object"], "nested": {"deep": "object"}},
        )
        self.assertEqual(check_contracts.shape({"items": [{"a": None}]}, depth=3), {"items": [{"a": "null"}]})


if __name__ == "__main__":
    unittest.main()
