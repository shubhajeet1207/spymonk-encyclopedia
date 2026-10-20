"""Search-index maintenance endpoints: clear and rebuild."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.api.errors import internal_error
from backend.api.schemas import ClearMemoryResponse, ReindexPlanResponse, ReindexResponse
from backend.api.security import require_allowed_origin
from backend.services.rate_limit import limit_maintenance

router = APIRouter(prefix="/api", tags=["memory"])
logger = logging.getLogger(__name__)


@router.delete(
    "/memory", response_model=ClearMemoryResponse, dependencies=[Depends(limit_maintenance)]
)
def clear_memory(request: Request) -> dict[str, object]:
    require_allowed_origin(request)
    logger.warning("Vector memory clear requested")
    try:
        from backend.core.store import clear_vectorstore

        clear_vectorstore()
    except Exception as exc:
        raise internal_error(logger, request, "Memory clear", exc, status_code=500) from exc
    logger.info("Vector memory cleared; source documents and topic audit records were retained")
    return {
        "cleared": True,
        "message": "The searchable vector index was cleared. PDF files and topic audit records were kept; rebuild the index to make them searchable again.",
    }


@router.get("/memory/reindex", response_model=ReindexPlanResponse)
def reindex_plan() -> dict[str, object]:
    """What a rebuild would index, without changing anything."""
    from backend.core.reindex import plan_reindex

    return plan_reindex().as_dict()


@router.post(
    "/memory/reindex", response_model=ReindexResponse, dependencies=[Depends(limit_maintenance)]
)
def reindex(request: Request) -> dict[str, object]:
    """Rebuild the vector index from audit files and stored PDFs, then swap it in."""
    require_allowed_origin(request)
    logger.warning("Vector index rebuild requested")
    try:
        from backend.core.reindex import run_reindex

        result = run_reindex()
    except HTTPException:
        raise
    except Exception as exc:
        raise internal_error(logger, request, "Index rebuild", exc, status_code=500) from exc
    return result.as_dict()
