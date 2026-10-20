"""Fail if the repository tracks files that must never be committed.

The repository is public. Personal data (the archive, uploads, snapshots,
logs, the index) and secrets belong on the machine or in the backup, never in
git. Also catches oversized files and unresolved merge conflicts.

    python scripts/ci/check_repo_policy.py
"""

from __future__ import annotations

import fnmatch
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAX_BYTES = 1024 * 1024

FORBIDDEN = {
    ".env": "secrets file; only .env.example belongs in git",
    "*/.env": "secrets file; only .env.example belongs in git",
    ".env.*": "secrets file; only .env.example belongs in git",
    "*.pem": "private key or certificate",
    "*.key": "private key",
    "*.p12": "private key bundle",
    "*.pfx": "private key bundle",
    "id_rsa*": "SSH private key",
    "id_ed25519*": "SSH private key",
    "chroma_db/*": "the search index (rebuildable, personal content)",
    "logs/*": "logs hold questions and claims",
    "data/*": "uploaded PDFs and source snapshots",
    "memory/*": "topic audits and unanswered records",
    "*.sqlite3": "database file",
    ".venv/*": "virtual environment",
    "*/node_modules/*": "installed packages",
    "frontend/dist/*": "build output",
}
ALLOWED = {".env.example", "frontend/.env.example", "data/pdfs/.gitkeep", "data/web_pdfs/.gitkeep", "data/web_html/.gitkeep", "memory/topics/.gitkeep"}
CONFLICT_MARKERS = (b"<<<<<<< ", b">>>>>>> ")


def tracked_files() -> list[str]:
    command = ["git", "ls-files", "-z"]  # noqa: S607 - git from PATH, fixed arguments
    output = subprocess.run(command, cwd=ROOT, capture_output=True, check=True).stdout  # noqa: S603
    return [name for name in output.decode("utf-8").split("\0") if name]


def violations(files: list[str]) -> list[str]:
    problems = []
    for name in files:
        if name in ALLOWED:
            continue
        for pattern, reason in FORBIDDEN.items():
            if fnmatch.fnmatchcase(name, pattern) or fnmatch.fnmatchcase(name.rsplit("/", 1)[-1], pattern):
                problems.append(f"{name}: {reason}")
                break
        else:
            path = ROOT / name
            if not path.is_file():
                continue
            size = path.stat().st_size
            if size > MAX_BYTES:
                problems.append(f"{name}: {size / 1024 / 1024:.1f} MB exceeds the {MAX_BYTES // 1024 // 1024} MB limit")
                continue
            content = path.read_bytes()
            if b"\0" not in content[:8000] and any(
                line.startswith(CONFLICT_MARKERS) for line in content.splitlines()
            ):
                problems.append(f"{name}: unresolved merge-conflict markers")
    return problems


def main() -> int:
    problems = violations(tracked_files())
    if problems:
        print("Files that must not be committed:")
        for problem in problems:
            print(f"  - {problem}")
        print("\nRemove them from git (git rm --cached <file>) and keep them in .gitignore.")
        return 1
    print("Repository policy: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
