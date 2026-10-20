"""Deploy SpyMonk to a Hugging Face Space (Docker) from this repository.

Uploads an allow-listed set of code files and the Space card; never `.env`,
data, memory, logs, or the index. Optionally sets the Space's secrets and
variables. Files in the Space that are no longer part of the app are removed.

    python scripts/deploy_hf_space.py --space your-name/spymonk \\
        --secret LLM_API_KEY --secret APP_API_TOKEN --secret BACKUP_HF_TOKEN \\
        --variable LLM_BASE_URL --variable LLM_MODEL \\
        --variable BACKUP_TARGET=hf-dataset --variable BACKUP_HF_REPO=your-name/spymonk-data

A secret or variable given without "=VALUE" takes its value from the
environment, then from `.env`. Log in first with `hf auth login`, or set
HF_TOKEN to a token that can write to your account. See
docs/DEPLOY_HF_SPACES.md.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPACE_CARD = ROOT / "deploy" / "huggingface" / "README.md"

# Everything the Docker build needs, and nothing else.
INCLUDE = (
    "Dockerfile",
    ".dockerignore",
    "app.py",
    "requirements.txt",
    "requirements-local-embeddings.txt",
    "constraints.txt",
    "backend/**/*.py",
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/index.html",
    "frontend/vite.config.js",
    "frontend/src/**/*",
)
_SKIPPED_PARTS = {"__pycache__", "node_modules", "dist"}
_FORBIDDEN = re.compile(r"(^|/)(\.env($|\.)|data/|memory/|logs/|chroma_db/|\.venv/)")


def deploy_files(root: Path = ROOT) -> dict[str, Path]:
    """The files to upload, keyed by their path in the Space."""
    files: dict[str, Path] = {}
    for pattern in INCLUDE:
        for path in root.glob(pattern):
            relative = path.relative_to(root).as_posix()
            if not path.is_file() or _SKIPPED_PARTS.intersection(path.relative_to(root).parts):
                continue
            if path.name.endswith(".test.js"):
                continue
            files[relative] = path
    files["README.md"] = root / "deploy" / "huggingface" / "README.md"
    leaked = [relative for relative in files if _FORBIDDEN.search(relative)]
    if leaked:
        raise SystemExit(f"Refusing to deploy files that must stay local: {leaked}")
    return files


def _env_file_values(root: Path) -> dict[str, str]:
    path = root / ".env"
    if not path.exists():
        return {}
    from dotenv import dotenv_values

    return {key: value for key, value in dotenv_values(path).items() if value is not None}


def resolve_settings(entries: list[str], local: dict[str, str]) -> dict[str, str]:
    """NAME=VALUE pairs; a bare NAME is read from the environment or .env."""
    settings: dict[str, str] = {}
    missing = []
    for entry in entries:
        name, separator, value = entry.partition("=")
        name = name.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise SystemExit(f"Invalid setting name: {name!r}")
        if not separator:
            value = os.environ.get(name) or local.get(name, "")
        if not value:
            missing.append(name)
        settings[name] = value
    if missing:
        raise SystemExit(f"No value found for: {', '.join(missing)}. Set them in the environment or .env.")
    return settings


def space_url(space: str) -> str:
    owner, _, name = space.partition("/")
    host = re.sub(r"[^a-z0-9-]", "-", f"{owner}-{name}".lower())
    return f"https://{host}.hf.space"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--space", required=True, help="Space ID, for example your-name/spymonk")
    parser.add_argument("--secret", action="append", default=[], help="Space secret NAME or NAME=VALUE (repeatable)")
    parser.add_argument("--variable", action="append", default=[], help="Space variable NAME or NAME=VALUE (repeatable)")
    parser.add_argument("--public", action="store_true", help="Create the Space as public (default: private)")
    parser.add_argument("--dry-run", action="store_true", help="List the files and settings; upload nothing")
    args = parser.parse_args(argv)

    files = deploy_files()
    local = _env_file_values(ROOT)
    secrets = resolve_settings(args.secret, local)
    variables = resolve_settings(args.variable, local)
    if args.dry_run:
        for relative in sorted(files):
            print(relative)
        print(f"\n{len(files)} files; secrets: {sorted(secrets) or 'none'}; variables: {sorted(variables) or 'none'}")
        return 0

    from huggingface_hub import CommitOperationAdd, CommitOperationDelete, HfApi

    api = HfApi()
    api.create_repo(args.space, repo_type="space", space_sdk="docker", private=not args.public, exist_ok=True)
    existing = set(api.list_repo_files(args.space, repo_type="space"))
    operations = [CommitOperationAdd(path_in_repo=relative, path_or_fileobj=str(path)) for relative, path in files.items()]
    operations += [CommitOperationDelete(path_in_repo=relative) for relative in sorted(existing - set(files) - {".gitattributes"})]
    api.create_commit(args.space, repo_type="space", operations=operations, commit_message="Deploy SpyMonk")
    for name, value in secrets.items():
        api.add_space_secret(args.space, name, value)
    for name, value in variables.items():
        api.add_space_variable(args.space, name, value)
    print(f"Deployed {len(files)} files. The Space rebuilds now (the first build takes about 10 minutes).")
    print(f"Space page: https://huggingface.co/spaces/{args.space}")
    print(f"App:        {space_url(args.space)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
