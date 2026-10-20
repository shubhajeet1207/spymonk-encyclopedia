"""Embedding factory shared by ingestion and retrieval."""

from __future__ import annotations

import logging
from functools import lru_cache

from langchain_core.embeddings import Embeddings

from backend.observability.logging import safe_exception_context

from . import config

logger = logging.getLogger(__name__)


def embedding_identity() -> tuple[str, str]:
    """(provider, model) that determines which vector space the index lives in."""
    if config.EMBEDDING_PROVIDER in {"openai", "llm"}:
        return "openai", config.OPENAI_EMBEDDING_MODEL
    return "huggingface", config.EMBEDDING_MODEL


@lru_cache(maxsize=1)
def get_embeddings() -> Embeddings:
    """Reuse the loaded embedding model across requests in this API process."""
    if config.EMBEDDING_PROVIDER in {"openai", "llm"}:
        logger.debug("Using configured OpenAI-compatible embedding provider")
        from langchain_openai import OpenAIEmbeddings

        kwargs: dict = {
            "model": config.OPENAI_EMBEDDING_MODEL,
            "api_key": config.require_embedding_api_key(),
        }
        if config.EMBEDDING_BASE_URL:
            kwargs["base_url"] = config.EMBEDDING_BASE_URL
        return OpenAIEmbeddings(**kwargs)

    if config.EMBEDDING_PROVIDER != "huggingface":
        raise ValueError(
            "EMBEDDING_PROVIDER must be 'huggingface' or 'openai'."
        )

    logger.debug("Using configured Hugging Face embedding provider")
    try:
        from langchain_huggingface import HuggingFaceEmbeddings
    except ImportError as exc:
        raise RuntimeError(
            "Local embeddings need the optional packages in requirements-local-embeddings.txt, "
            "or set EMBEDDING_PROVIDER=openai."
        ) from exc

    return HuggingFaceEmbeddings(
        model_name=config.EMBEDDING_MODEL,
        encode_kwargs={"normalize_embeddings": True},
    )


def warm_up_embeddings() -> None:
    """Load the model before the first user query (EMBEDDINGS_WARMUP=true)."""
    try:
        get_embeddings().embed_query("warm-up")
        logger.info("Embedding model warm-up completed")
    except Exception as exc:
        logger.warning("Embedding model warm-up failed (%s)", safe_exception_context(exc))
