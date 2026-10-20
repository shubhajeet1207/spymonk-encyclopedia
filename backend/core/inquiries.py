"""Questions research could not answer, kept as separate, reviewable records.

When a run verifies no claim, nothing enters the topic archive or the index.
Instead one JSON record per run is written to `memory/unanswered/`. It has two
audiences:

- `outcome`, `summary`, `process`, `claims`, `sources`, `suggestions`: a
  plain-language analysis for the person who asked, rendered as the PDF report;
- `developer`: the technical trace and automatic diagnostics, served as the
  developer log, for improving the answering pipeline.

Records never feed answers. They hold claims and short quotes (like topic
audits) but no prompts, full source text, credentials, or provider error text.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from backend.observability.logging import request_id_context, safe_exception_context

from . import config
from .evidence import EvidenceStatus
from .memory import canonical_topic, topic_slug, write_json_atomic
from .research_trace import ResearchTrace

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
USER_MESSAGE = "We cannot verify the claims for providing you the answers of your question."
_ID_PATTERN = re.compile(r"^\d{14}[0-9a-f]{16}$")
_QUOTE_CHARS = 300

REASON_LABELS = {
    "no_search_results": "No search results",
    "no_readable_sources": "No readable sources",
    "no_candidate_claims": "No clear answer in the sources",
    "no_independent_support": "Not confirmed by independent sources",
    "conflicting_evidence": "Sources disagree",
    "processing_failure": "Checks did not finish",
}

_STATUS_LABELS = {
    EvidenceStatus.SINGLE_SOURCE.value: "One source only",
    EvidenceStatus.CONFLICTED.value: "Sources disagree",
    EvidenceStatus.INCOMPLETE.value: "Check unfinished",
}


def inquiries_dir() -> Path:
    return config.MEMORY_DIR.parent / "unanswered"


def is_inquiry_id(value: object) -> bool:
    return isinstance(value, str) and bool(_ID_PATTERN.fullmatch(value))


def _new_id() -> str:
    # Time-ordered, so listing newest first is a filename sort.
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + secrets.token_hex(8)


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def _sites(origins: list[str]) -> str:
    if not origins:
        return ""
    if len(origins) == 1:
        return origins[0]
    return ", ".join(origins[:-1]) + f" and {origins[-1]}"


def _readable_sources(trace: ResearchTrace) -> list[dict[str, Any]]:
    return [source for source in trace.sources if source.get("read") == "ok"]


def _origin_counts(trace: ResearchTrace) -> Counter[str]:
    return Counter(source["origin_key"] for source in _readable_sources(trace) if source.get("origin_key"))


def _dominant_origin(trace: ResearchTrace) -> tuple[str, int] | None:
    """The site that supplied at least half of the readable sources, if any."""
    readable = len(_readable_sources(trace))
    origins = _origin_counts(trace)
    if not origins:
        return None
    site, count = origins.most_common(1)[0]
    return (site, count) if count > 1 and count * 2 >= readable else None


def _low_diversity(trace: ResearchTrace) -> bool:
    origins = _origin_counts(trace)
    return bool(origins) and (len(origins) < 3 or _dominant_origin(trace) is not None)


def _was(count: int) -> str:
    return "was" if count == 1 else "were"


def _explanation(reason: str, trace: ResearchTrace, claims: list[dict]) -> str:
    readable = len(_readable_sources(trace))
    if reason == "no_search_results":
        return "The web search returned no results for your question, so there was nothing to check."
    if reason == "no_readable_sources":
        results = trace.search.get("result_count", 0)
        return (
            f"The search found {_plural(results, 'result')}, but we could not read usable text from any of them."
        )
    if reason == "no_candidate_claims":
        return (
            f"We read {_plural(readable, 'source')}, but none stated a clear, quotable answer to your question."
        )
    if reason == "conflicting_evidence":
        return "The sources we found disagree with each other, so no answer could be confirmed."
    if reason == "processing_failure":
        return (
            "Some checks could not be completed this time, so no claim could be fully verified. "
            "Trying again later may help."
        )
    text = (
        f"We found {_plural(len(claims), 'possible answer')}, but none was confirmed by "
        "two independent websites."
    )
    dominant = _dominant_origin(trace)
    if dominant:
        site, count = dominant
        text += f" {count} of the {readable} sources came from {site}, which counts as one source."
    return text


def _claim_summary(claim: dict) -> dict[str, Any]:
    status = claim.get("validation_status", "")
    supporting = list(claim.get("supporting_origins") or [])
    contradicting = sorted(
        {item.get("origin_key") or item.get("origin", "") for item in claim.get("contradicting_evidence") or []}
        - {""}
    )
    if status == EvidenceStatus.CONFLICTED.value:
        detail = f"Supported by {_sites(supporting) or 'no source'}; contradicted by {_sites(contradicting)}."
    elif status == EvidenceStatus.INCOMPLETE.value:
        detail = "A cross-check could not finish, so this claim was held back."
    elif supporting:
        detail = f"Supported only by {_sites(supporting)}."
    else:
        detail = "No source we checked confirmed it."
    return {
        "id": claim.get("id", ""),
        "claim": claim.get("claim", ""),
        "status": status,
        "status_label": _STATUS_LABELS.get(status, "Not verified"),
        "detail": detail,
        "supporting_sites": supporting,
        "contradicting_sites": contradicting,
        "quote": str(claim.get("quote") or "")[:_QUOTE_CHARS],
        "source_url": claim.get("source_url", ""),
    }


def _process(reason: str, trace: ResearchTrace, claims: list[dict]) -> list[dict[str, str]]:
    """The run as the person who asked would describe it, step by step."""
    queries = trace.plan.get("queries") or trace.search.get("queries") or []
    results = trace.search.get("result_count", 0)
    collection = trace.collection
    readable = _readable_sources(trace)
    sites = len(_origin_counts(trace))
    candidates = trace.candidates.get("count", len(claims))
    checks = sum(entry.get("claims", 0) for entry in trace.cross_checks)
    confirmations = trace.counters.get("verdict_supports", 0)
    reached = {
        "no_search_results": 2,
        "no_readable_sources": 4,
        "no_candidate_claims": 5,
    }.get(reason, 7)
    if not candidates:
        reached = min(reached, 5)
    steps = [
        ("Planned the search", f"Prepared {_plural(len(queries), 'search query', 'search queries')}."),
        ("Searched the web", f"Found {_plural(results, 'result')}."),
        (
            "Collected sources",
            f"Downloaded {_plural(collection.get('html_pages', 0), 'web page')} and "
            f"{_plural(collection.get('pdfs_downloaded', 0), 'PDF')}.",
        ),
        ("Read the sources", f"{_plural(len(readable), 'source')} had readable text, from {_plural(sites, 'independent website')}."),
        ("Extracted claims", f"The AI found {_plural(candidates, 'possible answer')} backed by exact quotes."),
        (
            "Cross-checked claims",
            f"Ran {_plural(checks, 'check')} against the other sources; {confirmations} found a supporting quote.",
        ),
        ("Verified answers", "No claim was confirmed by two independent websites."),
    ]
    process = []
    for number, (label, detail) in enumerate(steps, start=1):
        if number < reached:
            status = "done"
        elif number == reached:
            status = "stopped"
            if reason == "no_search_results":
                detail = "The search returned no results."
            elif reason == "no_readable_sources":
                detail = "No collected source had enough readable text."
            elif reason == "no_candidate_claims":
                detail = "No source stated a clear, quotable answer."
            elif number == 5:
                detail = "Claim extraction did not finish for some sources, and no claim was found."
        else:
            status, detail = "not_reached", "Not reached."
        process.append({"step": str(number), "label": label, "detail": detail, "status": status})
    return process


def _suggestions(reason: str, trace: ResearchTrace) -> list[str]:
    suggestions = [
        "Ask a more specific question: name the product, place, person, or time period you mean."
    ]
    if reason in {"no_search_results", "no_readable_sources"}:
        suggestions.append("Try different keywords, or check that the subject is documented publicly.")
    if reason in {"no_independent_support", "no_candidate_claims", "conflicting_evidence"}:
        suggestions.append(
            "Add a PDF you trust on this subject to your library; your own documents can answer directly."
        )
    if _low_diversity(trace):
        suggestions.append(
            "Word the question so it reaches different kinds of sources, such as official documentation, "
            "news, or academic papers."
        )
    if reason == "processing_failure":
        suggestions.append("Try again in a few minutes; some checks did not finish this time.")
    return suggestions


def _configuration() -> dict[str, Any]:
    return {
        "llm_model": config.LLM_MODEL,
        "llm_provider_host": urlparse(config.LLM_BASE_URL or "").hostname or "default",
        "llm_temperature": config.LLM_TEMPERATURE,
        "llm_top_p": config.LLM_TOP_P,
        "llm_max_tokens": config.LLM_MAX_TOKENS,
        "llm_timeout_seconds": config.LLM_TIMEOUT_SECONDS,
        "llm_max_retries": config.LLM_MAX_RETRIES,
        "llm_concurrency": config.LLM_CONCURRENCY,
        "cross_check_batch_size": config.CROSS_CHECK_BATCH_SIZE,
        "max_candidate_claims": config.MAX_CANDIDATE_CLAIMS_PER_TOPIC,
        "max_cross_checks_per_claim": config.MAX_CROSS_CHECKS_PER_CLAIM,
        "min_independent_origins": config.MIN_INDEPENDENT_ORIGINS,
        "min_quote_chars": config.MIN_QUOTE_CHARS,
        "source_text_for_analysis_chars": config.SOURCE_TEXT_FOR_ANALYSIS_CHARS,
        "max_web_html": config.MAX_WEB_HTML,
        "max_web_pdfs": config.MAX_WEB_PDFS,
        "research_max_fetch_bytes": config.RESEARCH_MAX_FETCH_BYTES,
        "research_max_seconds": config.RESEARCH_MAX_SECONDS,
    }


def _error_types(trace: ResearchTrace) -> Counter[str]:
    errors: Counter[str] = Counter()
    for extraction in trace.extractions.values():
        if extraction.get("error_type"):
            errors[extraction["error_type"]] += 1
    for entry in trace.cross_checks:
        if entry.get("error_type"):
            errors[entry["error_type"]] += 1
        errors.update(entry.get("single_claim_errors") or [])
    return errors


def diagnose(reason: str, trace: ResearchTrace, claims: list[dict]) -> list[dict[str, str]]:
    """Findings a developer can act on, most decisive first."""
    findings: list[dict[str, str]] = []

    def add(code: str, severity: str, finding: str, suggestion: str) -> None:
        findings.append({"code": code, "severity": severity, "finding": finding, "suggestion": suggestion})

    readable = _readable_sources(trace)
    origins = _origin_counts(trace)
    if reason == "no_search_results":
        add(
            "no_search_results",
            "error",
            f"All {_plural(len(trace.search.get('queries') or []), 'search query', 'search queries')} returned no results.",
            "Check the search provider's availability and the planned queries; a licensed search API is more dependable than scraping.",
        )
    if reason == "no_readable_sources":
        statuses = Counter(source.get("read") for source in trace.sources)
        add(
            "no_readable_sources",
            "error",
            f"{trace.search.get('result_count', 0)} results gave {len(trace.sources)} fetched sources: "
            f"{statuses.get('error', 0)} failed to parse and {statuses.get('too_little_text', 0)} had under 200 characters.",
            "Inspect the fetch logs for this request ID; blocked hosts, script-rendered pages, and image-only PDFs are typical causes.",
        )
    if readable and _low_diversity(trace):
        site, count = origins.most_common(1)[0]
        add(
            "low_origin_diversity",
            "warning",
            f"{_plural(len(readable), 'readable source')} came from {_plural(len(origins), 'registrable domain')}; "
            f"{site} supplied {count}. Independent support needs {config.MIN_INDEPENDENT_ORIGINS} domains.",
            "Cap sources per registrable domain during collection and widen the query plan so claims have independent candidates.",
        )
    candidates = trace.candidates
    if candidates.get("cap_reached") and candidates.get("sources_reviewed", 0) < candidates.get("sources_available", 0):
        seeded = ", ".join(f"{origin} ({count})" for origin, count in Counter(candidates.get("seeded_by_origin") or {}).most_common())
        add(
            "candidate_cap_order_bias",
            "warning",
            f"The {candidates['cap']}-claim cap filled from the first {candidates['sources_reviewed']} of "
            f"{candidates['sources_available']} sources (seeded by {seeded}); later sources added no candidates.",
            "Pick candidates round-robin across sources or domains instead of first come, first served.",
        )
    single = [claim for claim in claims if claim.get("validation_status") == EvidenceStatus.SINGLE_SOURCE.value]
    unsupported = [claim for claim in single if not claim.get("supporting_origins")]
    if single:
        add(
            "single_origin_support",
            "warning",
            f"{_plural(len(single), 'claim')} lacked a second independent domain "
            f"({len(single) - len(unsupported)} supported by one domain, {len(unsupported)} by none).",
            "Claims confined to one site cannot be corroborated by design; increase source diversity rather than relaxing the rule.",
        )
    if unsupported:
        add(
            "unsupported_after_extraction",
            "warning",
            f"{_plural(len(unsupported), 'claim')} had a verbatim quote at extraction but no supporting verdict at cross-check, "
            "not even from the source it came from.",
            "Compare the extraction and cross-check prompts: the judge may reject paraphrased claims the extractor wrote.",
        )
    malformed = trace.counters.get("malformed_replies", 0)
    if malformed:
        add(
            "malformed_model_output",
            "warning",
            f"{config.LLM_MODEL} returned {_plural(malformed, 'reply', 'replies')} that {_was(malformed)} not valid JSON.",
            "Enable the provider's JSON mode, repair known defects before parsing, or use a model with better JSON compliance.",
        )
    errors = _error_types(trace)
    timeouts = sum(count for name, count in errors.items() if "Timeout" in name)
    if timeouts:
        add(
            "llm_timeouts",
            "warning",
            f"{_plural(timeouts, 'model call')} timed out after {config.LLM_TIMEOUT_SECONDS:g}s plus {config.LLM_MAX_RETRIES} retry.",
            "Raise LLM_TIMEOUT_SECONDS or choose a model with steadier latency.",
        )
    other_errors = {name: count for name, count in errors.items() if "Timeout" not in name and name != "LLMOutputError"}
    if other_errors:
        add(
            "llm_call_errors",
            "warning",
            "Model calls failed: " + ", ".join(f"{name} x{count}" for name, count in sorted(other_errors.items())) + ".",
            "Check provider status, rate limits, and the daily call budget for this request ID.",
        )
    rejected = trace.counters.get("verdict_quote_rejected", 0)
    if rejected:
        add(
            "non_verbatim_quotes",
            "info",
            f"{_plural(rejected, 'verdict')} {_was(rejected)} discarded because the model's quote was not verbatim in the source.",
            "Tighten the quote instruction or choose a model that copies text exactly.",
        )
    extraction_rejected = sum(extraction.get("rejected_quotes", 0) for extraction in trace.extractions.values())
    if extraction_rejected:
        add(
            "extraction_quote_mismatch",
            "info",
            f"{_plural(extraction_rejected, 'extracted claim')} {_was(extraction_rejected)} dropped because their quote did not match the source.",
            "Check whether the source text was clipped (SOURCE_TEXT_FOR_ANALYSIS_CHARS) or the model paraphrased quotes.",
        )
    incomplete = [claim for claim in claims if claim.get("validation_status") == EvidenceStatus.INCOMPLETE.value]
    if incomplete:
        add(
            "incomplete_checks",
            "warning",
            f"{_plural(len(incomplete), 'claim')} looked corroborated but missed a planned cross-check and were held back.",
            "See the failed cross-check calls below; a rerun usually completes them.",
        )
    conflicted = [claim for claim in claims if claim.get("validation_status") == EvidenceStatus.CONFLICTED.value]
    if conflicted:
        add(
            "conflicting_sources",
            "info",
            f"{_plural(len(conflicted), 'claim')} had contradicting evidence.",
            "Review the contradicting quotes; a direct documentary PDF can resolve a conflict (ADR-012).",
        )
    empty = [url for url, extraction in trace.extractions.items() if extraction.get("status") == "ok" and not extraction.get("raw_claims")]
    if empty:
        add(
            "sources_without_claims",
            "info",
            f"{_plural(len(empty), 'source')} yielded no claims about the question.",
            "Check whether the search results were on topic; refine query planning.",
        )
    return findings


def _developer_claim(claim: dict, trace: ResearchTrace) -> dict[str, Any]:
    facts = trace.claims.get(claim.get("id", ""), {})
    return {
        "id": claim.get("id", ""),
        "claim": claim.get("claim", ""),
        "status": claim.get("validation_status", ""),
        "supporting_origins": claim.get("supporting_origins") or [],
        "seed_origins": facts.get("seed_origins", []),
        "checked_origins": facts.get("checked_origins", []),
        "checked_sources": facts.get("checked_sources", []),
        "failed_checks": facts.get("failed_checks", []),
        "evidence": [
            {
                "origin_key": item.get("origin_key", ""),
                "source_url": item.get("source_url", ""),
                "verdict": item.get("verdict", ""),
                "quote": str(item.get("quote") or "")[:_QUOTE_CHARS],
                "reason": str(item.get("reason") or "")[:_QUOTE_CHARS],
            }
            for item in claim.get("evidence") or []
        ],
    }


def developer_section(
    trace: ResearchTrace, claims: list[dict], reason: str, failure_counts: dict[str, int]
) -> dict[str, Any]:
    """The technical trace of a run, with diagnostics; shared by every run log."""
    origins = _origin_counts(trace)
    return {
        "started_at": trace.started_at,
        "configuration": _configuration(),
        "timings_ms": {**trace.stage_ms, "total": trace.elapsed_ms()},
        "plan": trace.plan,
        "search": trace.search,
        "collection": trace.collection,
        "sources": [{**source, "extraction": trace.extractions.get(source["url"], {})} for source in trace.sources],
        "origin_diversity": {
            "distinct_origins": len(origins),
            "sources_per_origin": dict(origins.most_common()),
        },
        "candidates": trace.candidates,
        "cross_checks": {
            "calls": trace.cross_checks,
            "verdicts": {
                name.removeprefix("verdict_"): count
                for name, count in sorted(trace.counters.items())
                if name.startswith("verdict_")
            },
            "malformed_replies": trace.counters.get("malformed_replies", 0),
        },
        "failure_counts": dict(failure_counts),
        "claims": [_developer_claim(claim, trace) for claim in claims],
        "diagnostics": diagnose(reason, trace, claims),
        "events": trace.events,
    }


def build_record(
    topic: str,
    *,
    archive: dict,
    trace: ResearchTrace,
    reason: str,
    failure_counts: dict[str, int],
    previous_verified_claims: int,
) -> dict[str, Any]:
    claims = list(archive.get("unresolved_claims") or [])
    readable = _readable_sources(trace)
    origins = _origin_counts(trace)
    return {
        "schema_version": SCHEMA_VERSION,
        "id": _new_id(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "question": topic,
        "topic_key": canonical_topic(topic),
        "topic_slug": topic_slug(topic),
        "request_id": request_id_context.get(),
        "outcome": {
            "status": "unanswered",
            "message": USER_MESSAGE,
            "reason": reason,
            "reason_label": REASON_LABELS.get(reason, "Not verified"),
            "explanation": _explanation(reason, trace, claims),
            "verified_claims": 0,
            "unresolved_claims": len(claims),
            "held_back_claims": archive.get("held_back_claims", 0),
            "previous_verified_claims": previous_verified_claims,
            "failure_counts": dict(failure_counts),
        },
        "summary": {
            "search_queries": len(trace.plan.get("queries") or trace.search.get("queries") or []),
            "search_results": trace.search.get("result_count", 0),
            "sources_read": len(readable),
            "independent_sites": len(origins),
            "claims_found": len(claims),
            "claims_verified": 0,
            "duration_seconds": round(trace.elapsed_ms() / 1000, 1),
        },
        "process": _process(reason, trace, claims),
        "claims": [_claim_summary(claim) for claim in claims],
        "sources": [
            {
                "title": source.get("title") or source["url"],
                "url": source["url"],
                "site": source.get("origin_key") or source.get("origin", ""),
                "source_type": source.get("source_type", ""),
            }
            for source in readable
        ],
        "suggestions": _suggestions(reason, trace),
        "developer": developer_section(trace, claims, reason, failure_counts),
    }


def record_unanswered(
    topic: str,
    *,
    archive: dict,
    trace: ResearchTrace,
    reason: str,
    failure_counts: dict[str, int],
    previous_verified_claims: int,
) -> dict[str, Any]:
    """Build and store the record; a storage failure must not fail the research."""
    record = build_record(
        topic,
        archive=archive,
        trace=trace,
        reason=reason,
        failure_counts=failure_counts,
        previous_verified_claims=previous_verified_claims,
    )
    try:
        directory = inquiries_dir()
        directory.mkdir(parents=True, exist_ok=True)
        write_json_atomic(directory / f"{record['id']}.json", record, replace=False)
    except OSError as exc:
        logger.error("Could not store the unanswered-question record (%s)", safe_exception_context(exc))
        record["id"] = ""
    return record


def _valid(record: object) -> bool:
    return (
        isinstance(record, dict)
        and is_inquiry_id(record.get("id"))
        and isinstance(record.get("question"), str)
        and isinstance(record.get("outcome"), dict)
        and isinstance(record.get("claims"), list)
        and isinstance(record.get("developer"), dict)
    )


def load_inquiry(inquiry_id: str) -> dict[str, Any] | None:
    if not is_inquiry_id(inquiry_id):
        return None
    path = inquiries_dir() / f"{inquiry_id}.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, ValueError) as exc:
        logger.warning("Skipping unreadable unanswered-question record (%s)", safe_exception_context(exc))
        return None
    if not _valid(record) or record["id"] != inquiry_id:
        logger.warning("Skipping malformed unanswered-question record")
        return None
    return record


def list_inquiries(limit: int = 50) -> list[dict[str, Any]]:
    """Newest first; summaries only."""
    directory = inquiries_dir()
    if not directory.exists():
        return []
    summaries: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json"), reverse=True):
        if len(summaries) >= limit:
            break
        record = load_inquiry(path.stem)
        if record is None:
            continue
        summaries.append(
            {
                "id": record["id"],
                "question": record["question"],
                "created_at": record.get("created_at", ""),
                "reason": record["outcome"].get("reason", ""),
                "reason_label": record["outcome"].get("reason_label", ""),
                "claims_found": record.get("summary", {}).get("claims_found", 0),
                "sources_read": record.get("summary", {}).get("sources_read", 0),
            }
        )
    return summaries
