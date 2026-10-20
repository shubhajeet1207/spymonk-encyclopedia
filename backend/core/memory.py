"""Persistent topic audits, with collision-safe filenames and atomic writes.

Audits are the source of truth for web research. The vector index is derived
from them: a save records `index_state` ("pending", then "indexed", or "stale"
if indexing failed) so the reindex tool can reconcile the two stores.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import unicodedata
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.observability.logging import safe_exception_context

from . import config
from .evidence import ClaimRecord

logger = logging.getLogger(__name__)
_LEGACY_SLUG = re.compile(r"[^a-z0-9]+")
_MAX_PREFIX_CHARS = 48
IndexState = Literal["pending", "indexed", "stale"]

# list_topics() summaries keyed by file, reused until the file changes.
_summary_cache: dict[str, tuple[int, int, dict[str, Any] | None]] = {}
_summary_lock = threading.Lock()


class TopicArchive(BaseModel):
    """Minimum valid audit shape; retain additional provenance fields unchanged."""

    model_config = ConfigDict(extra="allow")

    topic: str = Field(min_length=1)
    claims: list[ClaimRecord]
    unresolved_claims: list[ClaimRecord] = Field(default_factory=list)
    index_state: IndexState | None = None

    @field_validator("topic")
    @classmethod
    def nonblank_topic(cls, value: str) -> str:
        if not canonical_topic(value):
            raise ValueError("Topic cannot be blank")
        return value


@dataclass(frozen=True)
class MigrationResult:
    source: Path
    destination: Path | None
    status: str


def canonical_topic(topic: str) -> str:
    """Normalize spelling variants for the audit filename, not the displayed title."""
    return " ".join(unicodedata.normalize("NFKC", topic).casefold().split())


def topic_slug(topic: str) -> str:
    key = canonical_topic(topic)
    readable: list[str] = []
    for char in key:
        if char == "_" or unicodedata.category(char)[0] in {"L", "M", "N"}:
            readable.append(char)
        elif readable and readable[-1] != "-":
            readable.append("-")
    prefix = "".join(readable).strip("-")[:_MAX_PREFIX_CHARS].rstrip("-") or "topic"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _legacy_topic_slug(topic: str) -> str:
    slug = _LEGACY_SLUG.sub("-", topic.lower()).strip("-")
    return slug[:80] or "topic"


def topic_path(topic: str) -> Path:
    config.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    return config.MEMORY_DIR / f"{topic_slug(topic)}.json"


def _read_archive(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        TopicArchive.model_validate(data)
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        logger.warning(
            "Skipping invalid topic audit (slug=%r %s)",
            path.stem,
            safe_exception_context(exc),
        )
        return None
    return data


def load_topic(topic: str) -> dict[str, Any] | None:
    path = topic_path(topic)
    if path.exists():
        data = _read_archive(path)
        if data is not None and canonical_topic(data["topic"]) == canonical_topic(topic):
            return data
        return None

    # Older installations can still read their audit before an explicit migration.
    legacy = config.MEMORY_DIR / f"{_legacy_topic_slug(topic)}.json"
    if not legacy.exists():
        return None
    data = _read_archive(legacy)
    if data is not None and canonical_topic(data["topic"]) == canonical_topic(topic):
        return data
    return None


def write_json_atomic(path: Path, data: dict[str, Any], *, replace: bool) -> None:
    """Persist a complete UTF-8 record before switching the visible filename."""
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.stem}-",
            suffix=".tmp",
            delete=False,
        ) as output:
            temporary = Path(output.name)
            json.dump(data, output, indent=2, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            # The migration must never clobber an existing new-format archive.
            os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_topic(archive: dict[str, Any]) -> Path:
    TopicArchive.model_validate(archive)
    path = topic_path(archive["topic"])
    if path.exists():
        existing = _read_archive(path)
        if existing is None or canonical_topic(existing["topic"]) != canonical_topic(archive["topic"]):
            raise ValueError("Refusing to replace an unrelated or unreadable topic audit")
        # A successful refresh must not erase the previous audit trail. If
        # archiving fails, leave the current file untouched rather than lose it.
        history_dir = config.MEMORY_DIR.parent / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        history_path = history_dir / f"{path.stem}-{_unique_suffix()}.json"
        write_json_atomic(history_path, existing, replace=False)
    updated = dict(archive)
    updated["slug"] = path.stem
    updated["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json_atomic(path, updated, replace=True)
    archive.update({"slug": updated["slug"], "updated_at": updated["updated_at"]})
    return path


def _unique_suffix() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{stamp}-{uuid.uuid4().hex}"


def mark_index_state(topic: str, state: IndexState) -> None:
    """Record whether this topic's answerable claims reached the vector index."""
    path = topic_path(topic)
    if not path.exists():
        logger.debug("No topic audit to update with an index state")
        return
    data = _read_archive(path)
    if data is None or canonical_topic(data["topic"]) != canonical_topic(topic):
        logger.warning("Topic audit could not be marked with an index state (slug=%r)", path.stem)
        return
    data["index_state"] = state
    write_json_atomic(path, data, replace=True)


def _summary(path: Path) -> dict[str, Any] | None:
    stat = path.stat()
    key = str(path)
    with _summary_lock:
        cached = _summary_cache.get(key)
    if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return cached[2]
    data = _read_archive(path)
    summary = (
        None
        if data is None
        else {
            "topic": data["topic"],
            "claims": len(data["claims"]),
            "index_state": data.get("index_state"),
            "path": str(path),
        }
    )
    with _summary_lock:
        _summary_cache[key] = (stat.st_mtime_ns, stat.st_size, summary)
    return summary


def list_topics() -> list[dict[str, Any]]:
    config.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    topics: list[dict[str, Any]] = []
    for path in sorted(config.MEMORY_DIR.glob("*.json")):
        try:
            summary = _summary(path)
        except OSError:
            continue
        if summary is not None:
            topics.append(dict(summary))
    return topics


def iter_topic_archives() -> Iterator[dict[str, Any]]:
    """Every readable audit, for rebuilding the index."""
    if not config.MEMORY_DIR.exists():
        return
    for path in sorted(config.MEMORY_DIR.glob("*.json")):
        data = _read_archive(path)
        if data is not None:
            yield data


def migrate_topic_files(directory: Path, *, apply: bool = False) -> list[MigrationResult]:
    """Plan, or explicitly apply, a no-clobber legacy filename migration."""
    if not directory.exists():
        return []
    results: list[MigrationResult] = []
    for source in sorted(directory.glob("*.json")):
        if source.is_symlink():
            results.append(MigrationResult(source, None, "skipped_symlink"))
            continue
        data = _read_archive(source)
        if data is None:
            results.append(MigrationResult(source, None, "invalid"))
            continue
        destination = directory / f"{topic_slug(data['topic'])}.json"
        if source == destination:
            results.append(MigrationResult(source, destination, "current"))
            continue
        if source.stem != _legacy_topic_slug(data["topic"]):
            results.append(MigrationResult(source, destination, "unexpected_name"))
            continue
        if destination.exists():
            results.append(MigrationResult(source, destination, "conflict"))
            continue
        if not apply:
            results.append(MigrationResult(source, destination, "ready"))
            continue
        migrated = dict(data)
        migrated["slug"] = destination.stem
        try:
            write_json_atomic(destination, migrated, replace=False)
            source.unlink()
        except (OSError, ValueError, TypeError) as exc:
            logger.warning(
                "Topic audit migration failed (slug=%r %s)",
                source.stem,
                safe_exception_context(exc),
            )
            results.append(MigrationResult(source, destination, "failed"))
        else:
            results.append(MigrationResult(source, destination, "migrated"))
    return results


def _main() -> None:
    parser = argparse.ArgumentParser(description="Preview or migrate legacy topic audit filenames.")
    parser.add_argument("--directory", type=Path, default=config.MEMORY_DIR)
    parser.add_argument("--apply", action="store_true", help="Apply the migration; default is dry-run.")
    args = parser.parse_args()
    for result in migrate_topic_files(args.directory, apply=args.apply):
        target = result.destination.name if result.destination is not None else "-"
        print(f"{result.status}: {result.source.name} -> {target}")


if __name__ == "__main__":
    _main()
