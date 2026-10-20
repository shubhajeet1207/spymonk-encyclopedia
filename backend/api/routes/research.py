"""Topic research endpoints: search planning and research jobs."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse

from backend.api.schemas import (
    ResearchJobResponse,
    ResearchRequest,
    ResearchResponse,
    SearchPlanRequest,
    SearchPlanResponse,
)
from backend.api.security import require_allowed_origin
from backend.core import config
from backend.services import archive
from backend.services.jobs import JobManager, JobRejected, ResearchJob, get_job_manager
from backend.services.rate_limit import limit_research

router = APIRouter(prefix="/api", tags=["research"])
logger = logging.getLogger(__name__)

_EVENT_WAIT_SECONDS = 15.0


@router.post(
    "/research/plan",
    response_model=SearchPlanResponse,
    dependencies=[Depends(limit_research)],
)
def create_search_plan(payload: SearchPlanRequest, request: Request) -> dict:
    """Call the LLM for search-query planning without fetching web sources."""
    require_allowed_origin(request)
    topic = payload.topic.strip()
    if not topic:
        raise HTTPException(status_code=422, detail="Enter a topic to prepare a search plan.")

    try:
        from backend.core.search_planner import plan_search_queries

        logger.info("LLM search planning started")
        plan = plan_search_queries(topic)
    except Exception as exc:
        status_code, detail = archive.describe_failure(exc, _request_id(request), operation="LLM search planning")
        raise HTTPException(status_code=status_code, detail=detail) from exc

    logger.info(
        "LLM search planning completed (planner=%s query_count=%s fallback_used=%s)",
        plan["planner"],
        len(plan["queries"]),
        plan["fallback_used"],
    )
    return {"topic": topic, **plan}


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def _submit(payload: ResearchRequest, request: Request, jobs: JobManager) -> tuple[ResearchJob, bool]:
    require_allowed_origin(request)
    topic = payload.topic.strip()
    if not topic:
        raise HTTPException(status_code=422, detail="Enter a topic to research.")
    try:
        archive.normalize_search_queries(payload.search_queries)
    except archive.InvalidRequestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    def run(job: ResearchJob) -> dict:
        return archive.lookup_or_research(
            topic,
            refresh=payload.refresh,
            reverify=payload.reverify,
            search_queries=payload.search_queries,
            on_progress=lambda event: job.add_event("pipeline", event),
            check_cancelled=job.check_cancelled,
        )

    try:
        return jobs.submit(topic=topic, request_id=_request_id(request), runner=run)
    except JobRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def _job_response(job: ResearchJob, *, existing: bool = False) -> dict:
    snapshot = job.snapshot()
    error = snapshot["error"]
    return {
        **snapshot,
        "existing": existing,
        "error": {"status": error["status"], "message": error["message"]} if error else None,
    }


def _job_or_404(jobs: JobManager, job_id: str) -> ResearchJob:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Research job not found. It may have expired.")
    return job


async def _event_stream(
    job: ResearchJob, jobs: JobManager, *, after: int = 0, cancel_on_disconnect: bool = False
) -> AsyncIterator[str]:
    """Server-sent events for one job, from event index `after` onwards."""
    position = after
    finished = False
    try:
        while True:
            events, terminal = await asyncio.to_thread(job.wait_events, position, _EVENT_WAIT_SECONDS)
            if not events and not terminal:
                yield ": keep-alive\n\n"
                continue
            for index, kind, data in events:
                position = index + 1
                payload = json.dumps(jsonable_encoder(data), ensure_ascii=False, separators=(",", ":"))
                yield f"id: {index}\nevent: {kind}\ndata: {payload}\n\n"
            if terminal:
                finished = True
                return
    finally:
        # The legacy stream owns its job: a closed connection means nobody is
        # waiting for the result, so stop spending on it.
        if cancel_on_disconnect and not finished and not job.terminal:
            jobs.cancel(job.id)


def _sse(stream: AsyncIterator[str], headers: dict[str, str] | None = None) -> StreamingResponse:
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", **(headers or {})},
    )


@router.post(
    "/research/jobs",
    response_model=ResearchJobResponse,
    status_code=202,
    dependencies=[Depends(limit_research)],
)
def create_research_job(
    payload: ResearchRequest,
    request: Request,
    response: Response,
    jobs: JobManager = Depends(get_job_manager),
) -> dict:
    """Start research in the background; watch it with the events endpoint."""
    job, existing = _submit(payload, request, jobs)
    response.headers["Location"] = f"/api/research/jobs/{job.id}"
    return _job_response(job, existing=existing)


@router.get("/research/jobs/{job_id}", response_model=ResearchJobResponse)
def get_research_job(job_id: str, jobs: JobManager = Depends(get_job_manager)) -> dict:
    return _job_response(_job_or_404(jobs, job_id))


@router.get("/research/jobs/{job_id}/events")
async def research_job_events(
    job_id: str,
    after: int = Query(default=0, ge=0),
    jobs: JobManager = Depends(get_job_manager),
) -> StreamingResponse:
    """Replay and follow a job's events; disconnecting does not cancel the job."""
    job = _job_or_404(jobs, job_id)
    return _sse(_event_stream(job, jobs, after=after))


@router.delete("/research/jobs/{job_id}", response_model=ResearchJobResponse)
def cancel_research_job(
    job_id: str, request: Request, jobs: JobManager = Depends(get_job_manager)
) -> dict:
    require_allowed_origin(request)
    _job_or_404(jobs, job_id)
    return _job_response(jobs.cancel(job_id))  # type: ignore[arg-type]


@router.post("/research", response_model=ResearchResponse, dependencies=[Depends(limit_research)])
def research(
    payload: ResearchRequest, request: Request, jobs: JobManager = Depends(get_job_manager)
) -> dict:
    """Run research and wait for the result (for clients that cannot stream)."""
    job, _ = _submit(payload, request, jobs)
    if not job.wait(timeout=config.RESEARCH_MAX_SECONDS + 60):
        jobs.cancel(job.id)
        raise HTTPException(status_code=504, detail="Research did not finish in time and was cancelled.")
    if job.status != "succeeded":
        error = job.error or {"status": 502, "detail": "Research failed."}
        raise HTTPException(status_code=error["status"], detail=error["detail"])
    return job.result  # type: ignore[return-value]


@router.post("/research/stream", dependencies=[Depends(limit_research)])
async def research_stream(
    payload: ResearchRequest, request: Request, jobs: JobManager = Depends(get_job_manager)
) -> StreamingResponse:
    """Start research and stream its stages; closing the stream cancels the job."""
    job, _ = await asyncio.to_thread(_submit, payload, request, jobs)
    return _sse(
        _event_stream(job, jobs, cancel_on_disconnect=True),
        headers={"X-Research-Job-ID": job.id},
    )
