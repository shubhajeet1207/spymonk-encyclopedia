"""Use the configured LLM to prepare focused web-search queries."""

from __future__ import annotations

import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage

from .llm_json import LLMOutputError, parse_llm_json
from .query import get_llm

logger = logging.getLogger(__name__)
MAX_SEARCH_QUERIES = 6
MAX_QUERY_CHARS = 250

_SYSTEM_PROMPT = """You create web-search queries for an evidence-grounded research system.
Return JSON only with this schema: {"queries": ["..."]}.
Create 3 to 6 distinct, concise queries that find primary sources, authoritative
references, and independent scholarly coverage where appropriate. Preserve named
entities and original-language terms from the topic. Do not answer the topic,
invent URLs, or claim that any source exists. Each item must be a search query."""


def _fallback_queries(topic: str) -> list[str]:
    topic = topic[:200].rstrip()
    return [
        f'"{topic}" primary source',
        f'{topic} official reference filetype:pdf',
        f'{topic} scholarly analysis',
    ]


def _extract_json(raw: str) -> dict | None:
    try:
        return parse_llm_json(raw)
    except LLMOutputError:
        return None


def plan_search_queries(topic: str) -> dict:
    """Return LLM-generated queries, using a deterministic fallback for bad JSON."""
    topic = (topic or "").strip()
    if not topic:
        raise ValueError("A topic is required to prepare a search plan.")

    response = get_llm().invoke(
        [
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=f"Prepare a search plan for this topic:\n{topic}"),
        ]
    )
    parsed = _extract_json(getattr(response, "content", "") or "")
    raw_queries = parsed.get("queries") if parsed else None
    queries: list[str] = []
    if isinstance(raw_queries, list):
        for item in raw_queries:
            if not isinstance(item, str):
                continue
            query = re.sub(r"\s+", " ", item).strip()[:MAX_QUERY_CHARS]
            if query and query.casefold() not in {value.casefold() for value in queries}:
                queries.append(query)
            if len(queries) >= MAX_SEARCH_QUERIES:
                break

    if len(queries) < 2:
        fallback_reason = "invalid_llm_plan" if parsed is None else "insufficient_llm_queries"
        logger.warning(
            "LLM search plan was unusable; using baseline query fallback (reason=%s)",
            fallback_reason,
        )
        return {
            "queries": _fallback_queries(topic),
            "planner": "baseline-fallback",
            "fallback_used": True,
        }

    return {"queries": queries, "planner": "llm", "fallback_used": False}
