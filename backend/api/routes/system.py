"""Health, runtime status, and saved-topic endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.api.schemas import StatusResponse, TopicsResponse
from backend.api.security import require_api_access

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/status", response_model=StatusResponse, dependencies=[Depends(require_api_access)])
def status() -> dict:
    from backend.core import config, memory, store
    from backend.core.embeddings import embedding_identity

    topics = memory.list_topics()
    local_pdfs = sorted(config.PDF_DIR.glob("*.pdf")) if config.PDF_DIR.exists() else []
    has_index = store.index_exists()
    health = (
        store.index_health()
        if has_index
        else {"state": "missing", "rebuild_recommended": False, "message": ""}
    )
    return {
        "api_online": True,
        "llm_configured": bool(config.LLM_API_KEY),
        "llm_model": config.LLM_MODEL,
        "embedding_provider": config.EMBEDDING_PROVIDER,
        "embedding_model": embedding_identity()[1],
        "vector_store_provider": config.VECTOR_STORE_PROVIDER,
        "index_exists": has_index,
        "index_state": health["state"],
        "index_rebuild_recommended": health["rebuild_recommended"],
        "index_message": health["message"],
        "topic_count": len(topics),
        "local_pdf_count": len(local_pdfs),
        "indexed_pdf_count": store.indexed_pdf_count() if has_index else 0,
        "max_pdf_bytes": config.MAX_PDF_BYTES,
    }


@router.get("/topics", response_model=TopicsResponse, dependencies=[Depends(require_api_access)])
def topics() -> dict:
    from backend.core import memory, store

    indexed = store.indexed_claim_counts()
    return {
        "items": [
            {
                "topic": item["topic"],
                "claims": item["claims"],
                "indexed_claims": indexed.get(memory.canonical_topic(item["topic"]), 0),
                "index_state": item.get("index_state"),
            }
            for item in memory.list_topics()
        ]
    }
