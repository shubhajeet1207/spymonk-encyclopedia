"""Evidence-status vocabulary, claim identity, and claim records shared across layers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from enum import Enum
from typing import Any, Literal

from langchain_core.documents import Document
from pydantic import BaseModel, ConfigDict, Field

from . import config


class EvidenceStatus(str, Enum):
    LOCAL_SOURCE = "local_source"
    CORROBORATED = "corroborated"
    PDF_RESOLVED = "pdf_resolved"
    SINGLE_SOURCE = "single_source"
    CONFLICTED = "conflicted"
    # Looked corroborated, but a planned cross-check failed, so a contradiction
    # could have been missed. Audit only until a later run checks it fully.
    INCOMPLETE = "incomplete"


ValidationStatus = Literal[
    "local_source",
    "corroborated",
    "pdf_resolved",
    "single_source",
    "conflicted",
    "incomplete",
    "unknown",
]

# Web claims are answerable only with these statuses (docs/DECISIONS.md).
ANSWERABLE_WEB_STATUSES = frozenset(
    {EvidenceStatus.CORROBORATED.value, EvidenceStatus.PDF_RESOLVED.value}
)

# Chroma filter for passages that may be shown to the answer model.
ELIGIBLE_EVIDENCE_WHERE: dict[str, Any] = {
    "$or": [
        {"origin": "local_pdf"},
        {
            "$and": [
                {"origin": "web"},
                {"validation_status": {"$in": sorted(ANSWERABLE_WEB_STATUSES)}},
            ]
        },
    ]
}


def is_answerable_web_status(value: object) -> bool:
    return value in ANSWERABLE_WEB_STATUSES


def has_independent_support(origins: Iterable[str]) -> bool:
    return len({origin for origin in origins if origin}) >= config.MIN_INDEPENDENT_ORIGINS


def normalize_claim_text(value: str) -> str:
    return " ".join((value or "").split()).casefold()


def claim_id(topic_key: str, claim: str) -> str:
    """Stable identity, so a refresh updates a claim instead of replacing it."""
    payload = f"claim|{topic_key}|{normalize_claim_text(claim)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    verdict: Literal["supports", "contradicts"]
    quote: str
    source_url: str = ""
    source_type: str = ""
    origin: str = ""
    origin_key: str = ""
    local_path: str = ""


class ClaimRecord(BaseModel):
    """Minimum audit claim shape; extra provenance fields are preserved unchanged."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1)
    claim: str = ""
    validation_status: ValidationStatus | None = None
    evidence: list[EvidenceRecord] = Field(default_factory=list)


def claim_document(record: dict) -> Document:
    """Build the searchable representation of one answerable web claim."""
    from .memory import canonical_topic

    page_content = (
        f"Topic: {record['topic']}\n"
        f"Claim: {record['claim']}\n"
        f"Supporting quote: {record['quote']}"
    )
    return Document(
        page_content=page_content,
        metadata={
            "source": record.get("source_title") or record.get("source_url") or "web source",
            "source_url": record.get("source_url", ""),
            "origin": "web",
            "local_path": record.get("local_path", ""),
            "quote": record.get("quote", "")[:1000],
            "topic": record.get("topic", ""),
            "topic_key": canonical_topic(record.get("topic", "")),
            "claim_id": record.get("id", ""),
            "page": str(record.get("page") or ""),
            "verified": "true",
            "validation_status": record.get(
                "validation_status", EvidenceStatus.SINGLE_SOURCE.value
            ),
            "source_type": record.get("source_type", "web_html"),
            "supporting_origins": ",".join(record.get("supporting_origins", [])),
            "evidence_json": json.dumps(record.get("evidence", []), ensure_ascii=False),
        },
    )
