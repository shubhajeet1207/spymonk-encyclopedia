"""A developer log for every research run, kept under `logs/research/`.

Saved, partially saved, and unanswered runs each get one JSON file with the
run's trace and diagnostics, so the answering pipeline can be studied across
runs, not only when it fails. Files are named by UTC time, outcome, and topic,
and the newest `RESEARCH_LOG_KEEP` are kept. Only the process that owns the
data directory writes them (the API enables this at startup), so importing
the pipeline, as tests do, writes nothing. Like unanswered-question records,
they hold claims, URLs, and short quotes, never prompts, source text, or
credentials.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

from backend.observability.logging import request_id_context, safe_exception_context

from . import config
from .inquiries import developer_section
from .memory import canonical_topic, topic_slug
from .research_trace import ResearchTrace

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
_directory: Path | None = None


def enable(directory: Path) -> None:
    global _directory
    _directory = directory


def disable() -> None:
    global _directory
    _directory = None


def _prune(directory: Path) -> None:
    keep = config.RESEARCH_LOG_KEEP
    if keep <= 0:
        return
    # Names sort by second only; modification time orders runs within one.
    paths = sorted(directory.glob("*.json"), key=lambda path: (path.stat().st_mtime_ns, path.name))
    for path in paths[:-keep]:
        path.unlink(missing_ok=True)


def write_run_log(
    topic: str,
    *,
    status: str,
    archive: dict,
    trace: ResearchTrace,
    failure_counts: dict[str, int],
) -> Path | None:
    """Store the run's developer log; a failure here never affects the run."""
    directory = _directory
    if directory is None:
        return None
    claims = list(archive.get("claims") or []) + list(archive.get("unresolved_claims") or [])
    reason = archive.get("failure_reason") or status
    now = datetime.now(timezone.utc)
    slug = re.sub(r"[^a-z0-9-]+", "", topic_slug(topic).lower())[:40].strip("-") or "topic"
    log = {
        "report": "spymonk.research_run",
        "schema_version": SCHEMA_VERSION,
        "created_at": now.isoformat(),
        "request_id": request_id_context.get(),
        "question": topic,
        "topic_key": canonical_topic(topic),
        "outcome": {
            "status": status,
            "reason": archive.get("failure_reason"),
            "verified_claims": len(archive.get("claims") or []) - archive.get("carried_over_claims", 0),
            "unresolved_claims": len(archive.get("unresolved_claims") or []),
            "held_back_claims": archive.get("held_back_claims", 0),
            "carried_over_claims": archive.get("carried_over_claims", 0),
            "inquiry_id": archive.get("inquiry_id") or None,
        },
        **developer_section(trace, claims, reason, failure_counts),
    }
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{now.strftime('%Y%m%dT%H%M%SZ')}-{status}-{slug}-{secrets.token_hex(3)}.json"
        path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
        _prune(directory)
    except OSError as exc:
        logger.warning("Could not write the research run log (%s)", safe_exception_context(exc))
        return None
    logger.info("Research run log written (status=%s file=%s)", status, path.name)
    return path
