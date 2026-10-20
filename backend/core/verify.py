"""Quote-level checks so LLM extractions cannot enter memory unverified."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from langchain_core.documents import Document

from . import config
from .evidence import has_independent_support, is_answerable_web_status
from .snapshot_paths import file_sha256, resolve_snapshot_path, snapshot_text
from backend.observability.logging import safe_exception_context

_WS = re.compile(r"\s+")
logger = logging.getLogger(__name__)


def normalize_text(value: str) -> str:
    return _WS.sub(" ", value or "").strip().lower()


def quote_is_supported(quote: str, source_text: str) -> bool:
    quote_n = normalize_text(quote)
    source_n = normalize_text(source_text)
    if len(quote_n) < config.MIN_QUOTE_CHARS:
        return False
    return quote_n in source_n


def source_text_from_pdf(path: Path) -> str:
    from .ingest import load_pdf

    pages = load_pdf(path)
    return "\n\n".join(page.page_content for page in pages if page.page_content)


def source_text_from_html(path: Path, url: str) -> str:
    from .scraper import extract_html_text

    _, text, _ = extract_html_text(path.read_bytes(), url, collect_pdf_links=False)
    return text


def source_text_from_record(record: dict) -> str:
    """Read the exact local source snapshot, or fetch it if the snapshot is missing."""
    from .scraper import (
        extract_html_text,
        fetch_url,
        looks_like_pdf,
        save_html,
        save_pdf,
        source_origin,
    )

    local_path = (record.get("local_path") or "").strip()
    source_type = record.get("source_type", "")
    url = (record.get("source_url") or "").strip()
    expected_sha256 = (record.get("snapshot_sha256") or "").strip()
    path = resolve_snapshot_path(local_path)
    if local_path and path is None:
        logger.warning("Evidence snapshot path is outside configured data storage; ignoring it")
    if path and path.exists():
        if expected_sha256 and file_sha256(path) != expected_sha256:
            # The bytes this evidence was verified against are gone; fail closed.
            logger.warning("Evidence snapshot does not match its recorded hash; evidence will be excluded")
            return ""
        if source_type == "pdf" or path.suffix.lower() == ".pdf":
            return snapshot_text(path, source_text_from_pdf)
        return snapshot_text(path, lambda snapshot: source_text_from_html(snapshot, url))
    if not url:
        logger.warning("Evidence snapshot is missing and no source URL is available; evidence will be excluded")
        return ""

    logger.info(
        "Evidence snapshot is missing; refetching original source (host=%s source_type=%s)",
        source_origin(url),
        source_type or "unknown",
    )
    final_url, body, content_type = fetch_url(url)
    if looks_like_pdf(final_url, content_type, body):
        return snapshot_text(save_pdf(final_url, body), source_text_from_pdf)
    save_html(final_url, body)
    _, text, _ = extract_html_text(body, final_url, collect_pdf_links=False)
    return text


def document_still_verified(
    document: Document,
    *,
    source_cache: dict[tuple[str, str, str], str] | None = None,
) -> bool:
    origin = document.metadata.get("origin")
    if origin == "local_pdf":
        return True
    if origin != "web":
        return False
    try:
        if not is_answerable_web_status(document.metadata.get("validation_status")):
            return False
        raw_evidence = document.metadata.get("evidence_json") or ""
        evidence = json.loads(raw_evidence) if raw_evidence else []
        if not isinstance(evidence, list):
            return False
        supporting = [item for item in evidence if item.get("verdict") == "supports"]
        # Recompute independence from source URLs. Legacy records may label
        # www/apex hosts as two origins, and stored origin fields are untrusted.
        from .scraper import registrable_origin

        origins = {
            registrable_origin(str(item.get("source_url") or ""))
            for item in supporting
        }
        if not has_independent_support(origins):
            return False
        if not evidence:
            return False
        for item in evidence:
            quote = item.get("quote") or ""
            key = (
                str(item.get("local_path") or ""),
                str(item.get("source_url") or ""),
                str(item.get("source_type") or ""),
            )
            if source_cache is not None and key in source_cache:
                source_text = source_cache[key]
            else:
                source_text = source_text_from_record(item)
                if source_cache is not None:
                    source_cache[key] = source_text
            if not quote_is_supported(quote, source_text):
                return False
        return True
    except Exception as exc:
        logger.warning(
            "Evidence re-verification failed; retrieved evidence was excluded (%s)",
            safe_exception_context(exc),
        )
        return False
