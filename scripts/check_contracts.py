"""Check that stored formats and the API contract change only on purpose.

SpyMonk has no SQL database; its "schemas" are the JSON it keeps on disk and
the API the frontend calls. This script snapshots them into `contracts/`:

- `storage.json`: the topic-audit JSON schema; the shape and version of
  unanswered-question records and research run logs; the index fingerprint
  and evidence-status vocabulary; and which files the backup keeps;
- `openapi.json`: the HTTP API contract.

    python scripts/check_contracts.py            # fail if anything changed
    python scripts/check_contracts.py --update   # accept the changes

Snapshots are generated from default settings; `.env` and app-related
environment variables are ignored.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
_SETTING_PREFIXES = (
    "SPYMONK_", "LLM_", "EMBEDDING", "OPENAI_", "NVIDIA_", "APP_", "BACKUP_", "SPACE_",
    "VECTOR_STORE", "CHROMA_", "RETRIEVE_", "REBUILD_", "REPORT_", "LOG_", "RESEARCH_",
)

HELP = """\
Stored formats or the API contract changed. If that is intended:
  1. run `python scripts/check_contracts.py --update` and commit `contracts/`;
  2. in the pull request, explain the change:
     - topic audits / records: existing files must still load, or add a migration;
     - index fingerprint or evidence vocabulary: existing indexes need a rebuild (ADR-019);
     - backup patterns: files no longer matched stop being backed up on hosted deployments;
     - API: the frontend and any client must keep working.
"""


def _use_defaults() -> None:
    os.environ["SPYMONK_SKIP_DOTENV"] = "1"
    for name in list(os.environ):
        if name.startswith(_SETTING_PREFIXES) and name != "SPYMONK_SKIP_DOTENV":
            del os.environ[name]
    sys.path.insert(0, str(ROOT))


def shape(value: object, depth: int = 2) -> object:
    """Key names and value types, to a fixed depth; values are left out."""
    if isinstance(value, dict):
        if depth == 0:
            return "object"
        return {key: shape(item, depth - 1) for key, item in sorted(value.items())}
    if isinstance(value, list):
        if depth == 0 or not value:
            return "array"
        return [shape(value[0], depth - 1)]
    if value is None:
        return "null"
    return type(value).__name__


def _sample_run() -> tuple[dict, object]:
    """A fixed, synthetic research run used to build sample records."""
    from backend.core.research_trace import ResearchTrace

    trace = ResearchTrace()
    trace.event({"step": "search", "status": "active", "message": "Searching."})
    trace.event({"step": "search", "status": "complete", "message": "Found 1 result."})
    trace.plan = {"planner": "llm", "fallback_used": False, "queries": ["q"]}
    trace.search = {"queries": ["q"], "result_count": 1, "results": [{"title": "T", "url": "https://a.example.org/x"}]}
    trace.collection = {"html_pages": 1, "pdf_links_found": 0, "pdfs_downloaded": 0, "bytes_downloaded": 1, "byte_budget": 2}
    trace.sources = [{
        "url": "https://a.example.org/x", "title": "T", "source_type": "web_html",
        "origin": "a.example.org", "origin_key": "example.org", "read": "ok", "text_chars": 500,
    }]
    trace.record_extraction("https://a.example.org/x", status="ok", raw_claims=1, accepted_claims=1, rejected_quotes=0, duration_ms=1.0)
    trace.candidates = {"count": 1, "cap": 12, "cap_reached": False, "sources_reviewed": 1, "sources_available": 1, "seeded_by_origin": {"example.org": 1}}
    trace.record_cross_check({"source": "https://a.example.org/x", "origin_key": "example.org", "claims": 1, "status": "ok", "failed_claims": 0, "duration_ms": 1.0})
    trace.count("verdict_supports")
    claim = {
        "id": "c1", "topic": "Sample", "claim": "A claim.", "quote": "A quote long enough.", "source_url": "https://a.example.org/x",
        "validation_status": "single_source", "supporting_origins": ["example.org"], "contradicting_evidence": [],
        "evidence": [{"origin_key": "example.org", "source_url": "https://a.example.org/x", "verdict": "supports", "quote": "q", "reason": "r"}],
        "cross_check_incomplete": False,
    }
    trace.claims["c1"] = {"seed_origins": ["example.org"], "checked_sources": ["https://a.example.org/x"], "checked_origins": ["example.org"], "failed_checks": []}
    archive = {"topic": "Sample", "claims": [], "unresolved_claims": [claim], "held_back_claims": 0, "failure_reason": "no_independent_support"}
    return archive, trace


def build() -> dict[str, str]:
    """The current contracts, as file name -> text."""
    _use_defaults()
    from backend.core import evidence, inquiries, run_log, store
    from backend.core.memory import TopicArchive
    from backend.main import app
    from backend.services import backup

    archive, trace = _sample_run()
    failures = {"source_read": 0, "extraction": 0, "cross_check": 0}
    record = inquiries.build_record(
        "Sample", archive=archive, trace=trace, reason="no_independent_support",
        failure_counts=failures, previous_verified_claims=0,
    )
    log = run_log.build_run_log("Sample", status="unanswered", archive=archive, trace=trace, failure_counts=failures)
    storage = {
        "topic_archive": TopicArchive.model_json_schema(),
        "unanswered_record": {"schema_version": inquiries.SCHEMA_VERSION, "shape": shape(record)},
        "research_run_log": {"schema_version": run_log.SCHEMA_VERSION, "shape": shape(log)},
        "index": {
            "fingerprint": store.index_fingerprint(),
            "evidence_statuses": sorted(status.value for status in evidence.EvidenceStatus),
            "answerable_web_statuses": sorted(evidence.ANSWERABLE_WEB_STATUSES),
            "eligibility_filter": evidence.ELIGIBLE_EVIDENCE_WHERE,
        },
        "backup_patterns": list(backup.BACKED_UP),
    }
    return {
        "storage.json": json.dumps(storage, indent=2, sort_keys=True) + "\n",
        "openapi.json": json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check stored formats and the API contract.")
    parser.add_argument("--update", action="store_true", help="write the current contracts to contracts/")
    args = parser.parse_args(argv)

    current = build()
    if args.update:
        CONTRACTS.mkdir(exist_ok=True)
        for name, text in current.items():
            (CONTRACTS / name).write_text(text, encoding="utf-8", newline="\n")
        print(f"Updated {', '.join(sorted(current))} in contracts/")
        return 0

    changed = False
    for name, text in sorted(current.items()):
        path = CONTRACTS / name
        committed = path.read_text(encoding="utf-8") if path.exists() else ""
        if committed.replace("\r\n", "\n") == text:
            print(f"ok       contracts/{name}")
            continue
        changed = True
        print(f"CHANGED  contracts/{name}")
        diff = difflib.unified_diff(
            committed.splitlines(), text.splitlines(), f"committed/{name}", f"current/{name}", lineterm="", n=2
        )
        for line in list(diff)[:80]:
            print("  " + line)
    if changed:
        print("\n" + HELP)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
