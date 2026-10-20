"""Typed request and response models for the HTTP boundary."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.core.evidence import ValidationStatus


class ResearchRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=500)
    refresh: bool = False
    reverify: bool = True
    search_queries: list[str] = Field(default_factory=list, max_length=6)


class SearchPlanRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=500)


class SearchPlanSummary(BaseModel):
    queries: list[str]
    planner: Literal["llm", "baseline-fallback", "provided"]
    fallback_used: bool = False


class SearchPlanResponse(SearchPlanSummary):
    topic: str


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2_000)
    reverify: bool = True
    research_on_miss: bool = False


class TopicSummary(BaseModel):
    topic: str
    claims: int
    indexed_claims: int = 0
    index_state: Literal["pending", "indexed", "stale"] | None = None


class TopicsResponse(BaseModel):
    items: list[TopicSummary]


class StatusResponse(BaseModel):
    api_online: bool
    llm_configured: bool
    llm_model: str
    embedding_provider: str
    embedding_model: str
    vector_store_provider: str
    index_exists: bool
    index_state: Literal["missing", "ok", "legacy", "stale", "mismatch"] = "missing"
    index_rebuild_recommended: bool = False
    index_message: str = ""
    topic_count: int
    local_pdf_count: int
    indexed_pdf_count: int
    max_pdf_bytes: int


class EvidenceSource(BaseModel):
    source: str = "unknown"
    page: int | str | None = None
    excerpt: str = ""
    url: str = ""
    origin: str = ""
    verified: str = ""
    validation_status: str = ""
    supporting_origins: str = ""
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class ClaimSummary(BaseModel):
    id: str
    claim: str
    supporting_origins: list[str] = Field(default_factory=list)


class UnresolvedClaim(BaseModel):
    id: str
    claim: str
    validation_status: ValidationStatus


class ResearchSource(BaseModel):
    url: str
    title: str
    source_type: str
    origin: str


class ResearchArchive(BaseModel):
    topic: str
    memory_updated: bool = False
    failure_reason: str | None = None
    held_back_claims: int = 0
    carried_over_claims: int = 0
    # Set when the run verified nothing: the unanswered-question record and
    # the message to show; earlier verified claims for the topic still answer.
    previous_verified_claims: int = 0
    inquiry_id: str | None = None
    outcome_message: str | None = None
    outcome_explanation: str | None = None
    claims: list[ClaimSummary] = Field(default_factory=list)
    unresolved_claims: list[UnresolvedClaim] = Field(default_factory=list)
    sources: list[ResearchSource] = Field(default_factory=list)


class ResearchResponse(BaseModel):
    cached: bool
    answer: str
    sources: list[EvidenceSource] = Field(default_factory=list)
    archive: ResearchArchive | None = None
    progress: list[str] = Field(default_factory=list)
    search_plan: SearchPlanSummary | None = None


class AskArchiveSummary(BaseModel):
    topic: str
    claims_count: int
    unresolved_count: int
    memory_updated: bool = False
    inquiry_id: str | None = None
    outcome_message: str | None = None
    outcome_explanation: str | None = None


class AskResponse(BaseModel):
    answer: str
    sources: list[EvidenceSource] = Field(default_factory=list)
    researched: bool = False
    archive: AskArchiveSummary | None = None


class JobError(BaseModel):
    status: int
    message: str


class ResearchJobResponse(BaseModel):
    job_id: str
    topic: str
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    existing: bool = False
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    event_count: int = 0
    result: ResearchResponse | None = None
    error: JobError | None = None


class InquirySummary(BaseModel):
    id: str
    question: str
    created_at: str
    reason: str
    reason_label: str
    claims_found: int = 0
    sources_read: int = 0


class InquiriesResponse(BaseModel):
    items: list[InquirySummary]


class DocumentsResponse(BaseModel):
    files: list[str]
    file_count: int
    chunk_count: int
    index_exists: bool
    duplicates: list[str] = Field(default_factory=list)


class ClearMemoryResponse(BaseModel):
    cleared: bool
    message: str


class ReindexPlanResponse(BaseModel):
    pdfs: list[str]
    duplicate_pdfs: list[str]
    topics: int
    answerable_claims: int
    index_state: str


class SkippedPdf(BaseModel):
    file: str
    reason: str


class ReindexResponse(BaseModel):
    indexed_pdfs: list[str]
    skipped_pdfs: list[SkippedPdf]
    duplicate_pdfs: list[str]
    topics: int
    claims: int
    documents: int
