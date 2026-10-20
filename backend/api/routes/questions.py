"""Evidence-grounded answer endpoint."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.api.schemas import AskRequest, AskResponse
from backend.api.security import require_allowed_origin
from backend.core import config, query
from backend.services import archive
from backend.services.jobs import JobManager, JobRejected, ResearchJob, get_job_manager
from backend.services.rate_limit import enforce, limit_ask, research_limiter

router = APIRouter(prefix="/api", tags=["answers"])
logger = logging.getLogger(__name__)


def _research_question(question: str, request: Request, jobs: JobManager, request_id: str) -> dict:
    """Run opted-in web research through the job queue and wait for it."""
    enforce(research_limiter, request)

    def run(job: ResearchJob) -> dict:
        # ask() already searched memory, so go straight to web research.
        return archive.lookup_or_research(
            question,
            skip_memory=True,
            on_progress=lambda event: job.add_event("pipeline", event),
            check_cancelled=job.check_cancelled,
        )

    try:
        job, _ = jobs.submit(topic=question, request_id=request_id, runner=run)
    except JobRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    if not job.wait(timeout=config.RESEARCH_MAX_SECONDS + 60):
        jobs.cancel(job.id)
        raise HTTPException(status_code=504, detail="Research did not finish in time and was cancelled.")
    if job.status != "succeeded":
        error = job.error or {"status": 502, "detail": "Research failed."}
        raise HTTPException(status_code=error["status"], detail=error["detail"])
    return job.result  # type: ignore[return-value]


@router.post("/ask", response_model=AskResponse, dependencies=[Depends(limit_ask)])
def ask_archive(
    payload: AskRequest, request: Request, jobs: JobManager = Depends(get_job_manager)
) -> dict:
    require_allowed_origin(request)
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="Enter a question.")
    request_id = getattr(request.state, "request_id", "unknown")

    try:
        result = query.ask(question, reverify=payload.reverify)
        researched = False
        summary = None
        if result.answer == query.NOT_FOUND:
            if payload.research_on_miss:
                logger.info("Memory lookup miss; falling back to public web research")
                response = _research_question(question, request, jobs, request_id)
                research_archive = response.get("archive") or {}
                researched = True
                summary = {
                    "topic": research_archive.get("topic", question),
                    "claims_count": len(research_archive.get("claims", [])),
                    "unresolved_count": len(research_archive.get("unresolved_claims", [])),
                    "memory_updated": research_archive.get("memory_updated", False),
                    "inquiry_id": research_archive.get("inquiry_id"),
                    "outcome_message": research_archive.get("outcome_message"),
                    "outcome_explanation": research_archive.get("outcome_explanation"),
                }
                if response.get("answer"):
                    # The research job already answered from the claims it saved.
                    result = query.EncyclopediaAnswer(answer=response["answer"], sources=response.get("sources", []))
                elif summary["memory_updated"] and summary["claims_count"]:
                    result = query.ask(question, reverify=payload.reverify)
                else:
                    logger.warning(
                        "Web research produced no verified claims; keeping the not-found answer"
                    )
            else:
                logger.info(
                    "Memory lookup miss and automatic research is disabled; returning not-found fallback"
                )
        else:
            logger.info("Memory lookup hit; answering from saved evidence")
        return {
            "answer": result.answer,
            "sources": result.sources,
            "researched": researched,
            "archive": summary,
        }
    except HTTPException:
        raise
    except Exception as exc:
        status_code, detail = archive.describe_failure(exc, request_id, operation="Answer")
        raise HTTPException(status_code=status_code, detail=detail) from exc
