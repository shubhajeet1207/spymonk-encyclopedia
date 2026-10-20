"""Questions research could not answer, and their downloadable reports."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response

from backend.api.schemas import InquiriesResponse
from backend.core import inquiries
from backend.observability.logging import log_internal_error
from backend.services import reports

router = APIRouter(prefix="/api", tags=["inquiries"])
logger = logging.getLogger(__name__)

InquiryId = Path(pattern=r"^\d{14}[0-9a-f]{16}$", description="Unanswered-question record ID")


def _record_or_404(inquiry_id: str) -> dict:
    record = inquiries.load_inquiry(inquiry_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Report not found.")
    return record


def _download(content: bytes, media_type: str, filename: str) -> Response:
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/inquiries", response_model=InquiriesResponse)
def list_unanswered_questions(limit: int = Query(default=50, ge=1, le=200)) -> dict:
    return {"items": inquiries.list_inquiries(limit)}


@router.get("/inquiries/{inquiry_id}/report.pdf", response_class=Response)
def download_report(request: Request, inquiry_id: str = InquiryId) -> Response:
    """The plain-language report for the person who asked."""
    record = _record_or_404(inquiry_id)
    try:
        content = reports.render_user_report(record)
    except Exception as exc:
        request_id = getattr(request.state, "request_id", "unknown")
        raise HTTPException(status_code=500, detail=log_internal_error(logger, "Report rendering", exc, request_id)) from exc
    logger.info("Unanswered-question report rendered (bytes=%s)", len(content))
    return _download(content, "application/pdf", reports.report_filename(record, "report", "pdf"))


@router.get("/inquiries/{inquiry_id}/developer-log", response_class=Response)
def download_developer_log(inquiry_id: str = InquiryId) -> Response:
    """The technical trace and diagnostics for improving the pipeline."""
    record = _record_or_404(inquiry_id)
    return _download(
        reports.render_developer_log(record),
        "application/json",
        reports.report_filename(record, "developer-log", "json"),
    )
