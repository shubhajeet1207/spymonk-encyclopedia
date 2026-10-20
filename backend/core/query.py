"""Retrieve verified encyclopedia memory and answer only from that context."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from . import config
from .evidence import ELIGIBLE_EVIDENCE_WHERE, is_answerable_web_status
from .llm_budget import budget as llm_budget
from .store import load_vectorstore, similarity_converter
from .verify import document_still_verified

NOT_FOUND = "Information not found in the custom encyclopedia archive."
logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an objective, academic Personal Encyclopedia.

Rules you must follow without exception:
1. Use only the CONTEXT excerpts below. They are the entire custom encyclopedia archive available to you.
2. Do not use prior knowledge, web knowledge, speculation, or unstated inference.
3. If the CONTEXT does not contain the facts needed to answer, reply with exactly this sentence and nothing else:
Information not found in the custom encyclopedia archive.
4. When the CONTEXT is sufficient, answer in a precise, formal, encyclopedic register.
5. Prefer quoting or closely paraphrasing the source. Mention page numbers and source URLs when metadata provides them.
6. Never invent citations, titles, dates, or figures.
7. Every web-derived fact in CONTEXT has quote-verified support from independent origins.
8. Unresolved single-source claims and conflicts are excluded from CONTEXT.

CONTEXT:
{context}
"""


@dataclass(frozen=True)
class EncyclopediaAnswer:
    answer: str
    sources: list[dict]


def _format_chunk(document: Document, index: int) -> str:
    source = document.metadata.get("source", "unknown")
    url = document.metadata.get("source_url") or ""
    origin = document.metadata.get("origin", "")
    validation = document.metadata.get("validation_status", "")
    supporting_origins = document.metadata.get("supporting_origins", "")
    page = document.metadata.get("page")
    page_label = page + 1 if isinstance(page, int) else page or "n/a"
    url_line = f" | URL: {url}" if url else ""
    return (
        f"[Excerpt {index} | Source: {source} | Page: {page_label} | Origin: {origin} | "
        f"Validation: {validation or 'user-provided'} | Supporting origins: {supporting_origins or 'n/a'}{url_line}]\n"
        f"{document.page_content.strip()}"
    )


def _source_record(document: Document) -> dict:
    try:
        evidence = json.loads(document.metadata.get("evidence_json") or "[]")
    except json.JSONDecodeError:
        evidence = []
    if not isinstance(evidence, list):
        evidence = []
    page = document.metadata.get("page")
    return {
        "source": document.metadata.get("source", "unknown"),
        "page": page + 1 if isinstance(page, int) else page,
        "excerpt": document.page_content.strip()[:400],
        "url": document.metadata.get("source_url") or "",
        "origin": document.metadata.get("origin", ""),
        "verified": document.metadata.get("verified", ""),
        "validation_status": document.metadata.get("validation_status")
        or ("local_source" if document.metadata.get("origin") == "local_pdf" else "unknown"),
        "supporting_origins": document.metadata.get("supporting_origins", ""),
        "evidence": evidence,
    }


class BudgetedChatModel:
    """Count each chat call against the daily LLM budget, then delegate."""

    def __init__(self, model: ChatOpenAI) -> None:
        self._model = model

    def invoke(self, *args, **kwargs):
        llm_budget.consume()
        return self._model.invoke(*args, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._model, name)


_llm_cache: dict[tuple, tuple[object, ChatOpenAI]] = {}
_llm_cache_lock = threading.Lock()


def get_llm() -> BudgetedChatModel:
    """Return a shared chat client; one HTTP connection pool serves every call."""
    kwargs: dict = {
        "model": config.LLM_MODEL,
        "api_key": config.require_llm_api_key(),
        "temperature": config.LLM_TEMPERATURE,
        "top_p": config.LLM_TOP_P,
        "max_tokens": config.LLM_MAX_TOKENS,
        "timeout": config.LLM_TIMEOUT_SECONDS,
        "max_retries": config.LLM_MAX_RETRIES,
    }
    if config.LLM_BASE_URL:
        kwargs["base_url"] = config.LLM_BASE_URL
    if config.LLM_DISABLE_THINKING:
        kwargs["extra_body"] = {"chat_template_kwargs": {"thinking": False}}
    key = tuple(sorted((name, repr(value)) for name, value in kwargs.items()))
    factory = ChatOpenAI
    with _llm_cache_lock:
        cached = _llm_cache.get(key)
        # A replaced factory (tests patch ChatOpenAI) must not reuse an old client.
        if cached is None or cached[0] is not factory:
            cached = (factory, factory(**kwargs))
            _llm_cache.clear()
            _llm_cache[key] = cached
    return BudgetedChatModel(cached[1])


def retrieve(question: str, k: int = config.RETRIEVE_K) -> list[Document]:
    """Semantic search over eligible passages, dropping weak and duplicate matches."""
    store = load_vectorstore()
    to_similarity = similarity_converter()
    results = store.similarity_search_with_score(
        question, k=max(k, config.RETRIEVE_FETCH_K), filter=ELIGIBLE_EVIDENCE_WHERE
    )
    documents: list[Document] = []
    seen: set[str] = set()
    for document, distance in results:
        if to_similarity(distance) < config.RETRIEVE_MIN_SIMILARITY:
            continue
        text = " ".join(document.page_content.split())
        if text in seen:
            continue
        seen.add(text)
        documents.append(document)
        if len(documents) >= k:
            break
    return documents


def retrieve_eligible_documents(question: str, k: int = config.RETRIEVE_K) -> list[Document]:
    """Search indexed memory without fetching sources or calling the answer LLM."""
    question = (question or "").strip()
    if not question:
        raise ValueError("A non-empty question is required.")

    try:
        documents = retrieve(question, k=k)
    except FileNotFoundError:
        logger.info("Memory index is unavailable; falling back to the not-found answer")
        return []
    retrieved_count = len(documents)
    # The store query already filters; this repeats the rule as defence in depth.
    documents = [
        doc
        for doc in documents
        if doc.metadata.get("origin") == "local_pdf"
        or (
            doc.metadata.get("origin") == "web"
            and is_answerable_web_status(doc.metadata.get("validation_status"))
        )
    ]
    if not documents:
        logger.info(
            "No eligible evidence was retrieved (retrieved_count=%s)",
            retrieved_count,
        )
    return documents


def reverify_documents(documents: list[Document]) -> list[Document]:
    """Recheck stored web evidence only when the caller requests it."""
    source_cache: dict[tuple[str, str, str], str] = {}
    verified = [
        doc for doc in documents if document_still_verified(doc, source_cache=source_cache)
    ]
    if len(verified) < len(documents):
        logger.warning(
            "Evidence re-verification excluded retrieved passages (excluded_count=%s)",
            len(documents) - len(verified),
        )
    return verified


def answer_from_documents(question: str, documents: list[Document]) -> EncyclopediaAnswer:
    """Compose an answer from already selected and optionally checked passages."""
    if not documents:
        return EncyclopediaAnswer(answer=NOT_FOUND, sources=[])

    context = "\n\n".join(_format_chunk(doc, i + 1) for i, doc in enumerate(documents))
    messages = [
        SystemMessage(content=SYSTEM_PROMPT.format(context=context)),
        HumanMessage(content=question),
    ]
    response = get_llm().invoke(messages)
    answer = (response.content or "").strip() or NOT_FOUND
    if answer == NOT_FOUND:
        logger.info("Answer generation found no sufficient facts; returning not-found fallback")
    return EncyclopediaAnswer(
        answer=answer,
        sources=[_source_record(doc) for doc in documents],
    )


def ask(question: str, k: int = config.RETRIEVE_K, *, reverify: bool = True) -> EncyclopediaAnswer:
    question = (question or "").strip()
    documents = retrieve_eligible_documents(question, k=k)
    if reverify and documents:
        documents = reverify_documents(documents)
    return answer_from_documents(question, documents)


if __name__ == "__main__":
    import sys

    prompt = " ".join(sys.argv[1:]).strip()
    if not prompt:
        print('Usage: python -m backend.core.query "your question"')
        raise SystemExit(1)
    result = ask(prompt)
    print(result.answer)
    if result.sources and result.answer != NOT_FOUND:
        print("\nSources:")
        for src in result.sources:
            extra = f" {src['url']}" if src.get("url") else ""
            print(f"- {src['source']} (page {src['page']}){extra}")
