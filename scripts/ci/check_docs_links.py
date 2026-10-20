"""Fail if a Markdown file links to a repository path that does not exist.

Checks relative links in every tracked `.md` file; web links and in-page
anchors are not fetched.

    python scripts/ci/check_docs_links.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
# [text](target) and ![alt](target); stops at whitespace or a closing parenthesis.
LINK = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
FENCE = re.compile(r"^(```|~~~)")


def markdown_files() -> list[Path]:
    command = ["git", "ls-files", "-z", "*.md"]  # noqa: S607 - git from PATH, fixed arguments
    output = subprocess.run(command, cwd=ROOT, capture_output=True, check=True).stdout  # noqa: S603
    return [ROOT / name for name in output.decode("utf-8").split("\0") if name]


def broken_links(path: Path) -> list[str]:
    problems = []
    in_fence = False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if FENCE.match(line.strip()):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for target in LINK.findall(line):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.IGNORECASE) or target.startswith("#"):
                continue  # web, mail, and in-page links
            relative = unquote(target.split("#", 1)[0])
            if not relative:
                continue
            resolved = (ROOT / relative.lstrip("/")) if relative.startswith("/") else (path.parent / relative)
            if not resolved.exists():
                problems.append(f"{path.relative_to(ROOT).as_posix()}:{number}: {target}")
    return problems


def main() -> int:
    problems = [problem for path in markdown_files() for problem in broken_links(path)]
    if problems:
        print("Broken relative links:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("Documentation links: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
