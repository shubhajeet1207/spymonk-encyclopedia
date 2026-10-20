"""Memory-first lookup and web research, independent of HTTP.

Routes and background jobs both call these functions. Core modules are
accessed as module attributes (`store.index_exists()`), not imported names, so
a single patch in tests affects every caller.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from backend.core import config, query, research, search_planner, store
from backend.core.llm_budget import LLMBudgetExceeded
from backend.core.research import ResearchCancelled
from backend.core.research_trace import ResearchTrace
from backend.core.scraper import SearchProviderUnavailableError
from backend.core.store import IndexCompatibilityError
from backend.observability.logging import log_internal_error, safe_exception_context

logger = logging.getLogger(__name__)

ProgressFn = Callable[[dict[str, object]], None]
CancelCheck = Callable[[], None]

MAX_SEARCH_QUERIES = 6
MAX_QUERY_CHARS = 250


class InvalidRequestError(ValueError):
    """The request is well-formed JSON but cannot be processed as given."""


def normalize_search_queries(queries: list[str] | None) -> list[str]:
    cleaned = list(
        dict.fromkeys(query.strip()[:MAX_QUERY_CHARS] for query in queries or [] if query.strip())
    )[:MAX_SEARCH_QUERIES]
    if any(len(query) < 2 for query in cleaned):
        raise InvalidRequestError("Search queries must contain at least two characters.")
    return cleaned


def _llm_provider_failure(exc: BaseException) -> tuple[int, str] | None:
    """Explain chat-provider failures; these come from configuration or load, not our code."""
    import openai

    model = config.LLM_MODEL
    # APITimeoutError subclasses APIConnectionError, so it is checked first.
    if isinstance(exc, openai.APITimeoutError):
        return 504, (
            f"The language model ({model}) did not respond within {config.LLM_TIMEOUT_SECONDS:g} seconds. "
            "The provider may be overloaded: try again later, raise LLM_TIMEOUT_SECONDS, "
            "or set LLM_MODEL to a model that responds."
        )
    if isinstance(exc, openai.APIConnectionError):
        return 502, "Could not connect to the language model provider. Check LLM_BASE_URL and the network."
    if isinstance(exc, openai.RateLimitError):
        return 429, "The language model provider is rate-limiting requests. Wait a minute and try again."
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return 502, "The language model provider rejected the configured API key (LLM_API_KEY)."
    if isinstance(exc, openai.NotFoundError):
        return 502, (
            f"The model {model} is not available to this account at the configured provider. "
            "Set LLM_MODEL to an available model."
        )
    return None


def describe_failure(exc: BaseException, request_id: str, operation: str = "Research") -> tuple[int, str]:
    """Map a failure to an HTTP status and a detail that is safe to show users."""
    provider_failure = _llm_provider_failure(exc)
    if provider_failure is not None:
        logger.warning("%s stopped: language model call failed (%s)", operation, safe_exception_context(exc))
        return provider_failure
    if isinstance(exc, InvalidRequestError):
        return 422, str(exc)
    if isinstance(exc, SearchProviderUnavailableError):
        logger.warning(
            "Research stopped because web search returned no usable results "
            "(failed_query_count=%s query_count=%s)",
            exc.failed_query_count,
            exc.query_count,
        )
        return 503, "Public web search is temporarily unavailable; no sources were collected. Please try again."
    if isinstance(exc, ResearchCancelled):
        if exc.reason == "timed out":
            return 504, "Research exceeded its time limit and was stopped; nothing was saved."
        return 409, "Research was cancelled; nothing was saved."
    if isinstance(exc, LLMBudgetExceeded):
        return 429, str(exc)
    if isinstance(exc, IndexCompatibilityError):
        return 409, str(exc)
    return 502, log_internal_error(logger, operation, exc, request_id)


def research_response(archive: dict, progress: list[str], search_plan: dict) -> dict:
    """The API's summary of a research run; local paths and quotes stay in the audit."""
    return {
        "cached": False,
        "answer": "",
        "sources": [],
        "archive": {
            "topic": archive["topic"],
            "memory_updated": archive.get("memory_updated", False),
            "failure_reason": archive.get("failure_reason"),
            "held_back_claims": archive.get("held_back_claims", 0),
            "carried_over_claims": archive.get("carried_over_claims", 0),
            "previous_verified_claims": archive.get("previous_verified_claims", 0),
            "inquiry_id": archive.get("inquiry_id") or None,
            "outcome_message": archive.get("outcome_message") or None,
            "outcome_explanation": archive.get("outcome_explanation") or None,
            "claims": [
                {
                    "id": claim["id"],
                    "claim": claim["claim"],
                    "supporting_origins": claim["supporting_origins"],
                }
                for claim in archive["claims"]
            ],
            "unresolved_claims": [
                {
                    "id": claim["id"],
                    "claim": claim["claim"],
                    "validation_status": claim["validation_status"],
                }
                for claim in archive["unresolved_claims"]
            ],
            "sources": [
                {
                    "url": source["url"],
                    "title": source["title"],
                    "source_type": source["source_type"],
                    "origin": source["origin"],
                }
                for source in archive["sources"]
            ],
        },
        "progress": progress,
        "search_plan": search_plan,
    }


def _research(
    topic: str,
    queries: list[str],
    emit: Callable[..., None],
    publish: ProgressFn,
    check: CancelCheck,
    trace: ResearchTrace,
) -> dict:
    if queries:
        search_plan = {"queries": queries, "planner": "provided", "fallback_used": False}
        logger.info("Using supplied search plan (query_count=%s)", len(queries))
        emit("plan", "complete", f"Using the prepared plan with {len(queries)} queries.", current=len(queries), total=len(queries))
    else:
        emit("plan", "active", "Preparing focused search queries with the configured LLM.")
        check()
        search_plan = search_planner.plan_search_queries(topic)
        logger.info(
            "Search plan prepared (planner=%s query_count=%s fallback_used=%s)",
            search_plan["planner"],
            len(search_plan["queries"]),
            search_plan["fallback_used"],
        )
        emit(
            "plan",
            "complete",
            f"Prepared {len(search_plan['queries'])} queries ({search_plan['planner']}).",
            current=len(search_plan["queries"]),
            total=len(search_plan["queries"]),
        )

    progress: list[str] = [
        f"Prepared {len(search_plan['queries'])} search queries ({search_plan['planner']})."
    ]
    trace.plan = {
        "planner": search_plan["planner"],
        "fallback_used": search_plan["fallback_used"],
        "queries": list(search_plan["queries"]),
    }
    emit("search", "active", "Starting public source search.")
    archive = research.research_topic(
        topic,
        status=progress.append,
        search_queries=search_plan["queries"],
        on_progress=publish,
        check_cancelled=check,
        trace=trace,
    )
    logger.info(
        "Research completed (memory_updated=%s verified_claims=%s unresolved_claims=%s sources=%s)",
        archive.get("memory_updated", False),
        len(archive["claims"]),
        len(archive["unresolved_claims"]),
        len(archive["sources"]),
    )
    return research_response(archive, progress, search_plan)


def _answer_from_new_claims(
    topic: str, response: dict, emit: Callable[..., None], check: CancelCheck
) -> None:
    """Answer the question from the claims this run just saved.

    The claims are already stored, so nothing here may fail or cancel the job:
    any problem skips the answer and says how to get it from the archive.
    """
    archive = response["archive"]
    if not (archive.get("memory_updated") and archive.get("claims")):
        emit("answer", "skipped", "Skipped: no verified claim to answer from.")
        return
    emit("answer", "active", "Preparing an answer from the newly verified claims.")
    try:
        check()
        # Freshly verified against their snapshots, so no re-verification here.
        documents = store.find_topic_documents(topic)
        if not documents:
            emit("answer", "skipped", "The claims were saved but are not searchable yet; rebuild the index, then ask again.")
            return
        result = query.answer_from_documents(topic, documents)
    except ResearchCancelled:
        emit("answer", "skipped", "Skipped: research was stopped after the claims were saved.")
        return
    except Exception as exc:
        logger.warning("Research saved its claims, but the answer could not be prepared (%s)", safe_exception_context(exc))
        emit("answer", "skipped", "The claims were saved, but the answer could not be prepared. Ask the question in the chat to try again.")
        return
    if result.answer == query.NOT_FOUND:
        emit("answer", "complete", "The verified claims do not answer this question directly; review them below.")
        return
    response["answer"] = result.answer
    response["sources"] = result.sources
    logger.info("Answer prepared from newly saved claims (source_count=%s)", len(result.sources))
    emit("answer", "complete", "Answer prepared from the newly verified claims.")


def lookup_or_research(
    topic: str,
    *,
    refresh: bool = False,
    reverify: bool = True,
    search_queries: list[str] | None = None,
    skip_memory: bool = False,
    on_progress: ProgressFn | None = None,
    check_cancelled: CancelCheck | None = None,
) -> dict:
    """Answer from saved evidence when possible; otherwise research the public web."""
    topic = (topic or "").strip()
    if not topic:
        raise InvalidRequestError("Enter a topic to research.")
    queries = normalize_search_queries(search_queries)
    check = check_cancelled or (lambda: None)

    active_step = "memory"
    stage_started: dict[str, float] = {}

    def publish(event: dict[str, object]) -> None:
        nonlocal active_step
        step = event.get("step")
        state = event.get("status")
        if isinstance(step, str) and state == "active":
            active_step = step
            stage_started.setdefault(step, time.perf_counter())
        elif isinstance(step, str) and state in {"complete", "skipped", "failed"}:
            started = stage_started.pop(step, None)
            if started is not None:
                duration_ms = round((time.perf_counter() - started) * 1000, 1)
                event["duration_ms"] = duration_ms
                logger.info(
                    "Research stage finished (stage=%s status=%s duration_ms=%.1f)",
                    step,
                    state,
                    duration_ms,
                )
        if on_progress:
            on_progress(event)

    # Stages the research core runs record into the trace themselves; stages
    # this function runs (memory lookup, planning) are recorded here.
    trace = ResearchTrace()

    def emit(step: str, status: str, message: str, **extra: object) -> None:
        event = {"step": step, "status": status, "message": message, **extra}
        trace.event(event)
        publish(event)

    try:
        emit("memory", "active", "Checking whether searchable archive memory is available.")
        if refresh or skip_memory:
            reason = "a source refresh was requested" if refresh else "memory was already checked"
            logger.info("Skipping memory lookup because %s", reason)
            emit("memory", "skipped", f"Skipped because {reason}.")
            for step in ("retrieve", "reverify", "compose"):
                emit(step, "skipped", f"Skipped because {reason}.")
        elif store.index_exists():
            emit("memory", "complete", "Searchable archive memory is available.")
            emit("retrieve", "active", "Checking the exact-topic index for verified claims.")
            documents = store.find_topic_documents(topic)
            lookup_mode = "exact-topic index"
            if not documents:
                emit("retrieve", "active", "No exact topic match; searching related archive passages.")
                documents = query.retrieve_eligible_documents(topic)
                lookup_mode = "semantic search"
            logger.info(
                "Archive evidence retrieval completed (mode=%s eligible_count=%s)",
                lookup_mode,
                len(documents),
            )
            emit(
                "retrieve",
                "complete",
                f"Found {len(documents)} eligible passages via {lookup_mode}.",
                current=len(documents),
                total=len(documents),
            )
            check()
            if documents and reverify:
                emit("reverify", "active", "Rechecking saved evidence quotes and source snapshots.")
                documents = query.reverify_documents(documents)
                emit(
                    "reverify",
                    "complete",
                    f"Retained {len(documents)} supported passages.",
                    current=len(documents),
                    total=len(documents),
                )
            else:
                emit("reverify", "skipped", "No passages needed rechecking." if not documents else "Source rechecking was disabled.")
            if documents:
                emit("compose", "active", "Preparing an answer from the retrieved passages.")
                check()
                cached = query.answer_from_documents(topic, documents)
                emit("compose", "complete", "Archive answer prepared." if cached.answer != query.NOT_FOUND else "Retrieved passages did not answer this topic.")
                if cached.answer != query.NOT_FOUND:
                    logger.info("Memory lookup hit; returning saved evidence without web research")
                    for step in ("plan", "search", "collect", "extract", "verify", "save", "answer"):
                        emit(step, "skipped", "Skipped because saved memory already answered this topic.")
                    return {
                        "cached": True,
                        "answer": cached.answer,
                        "sources": cached.sources,
                        "archive": None,
                        "progress": [],
                    }
            else:
                emit("compose", "skipped", "No eligible evidence was found in the archive.")
            logger.info("Memory lookup miss; falling back to public web research")
        else:
            logger.info("Memory index is empty; falling back to public web research")
            emit("memory", "complete", "No saved index yet; starting public research.")
            for step in ("retrieve", "reverify", "compose"):
                emit(step, "skipped", "Skipped because the archive has no searchable index.")

        check()
        response = _research(topic, queries, emit, publish, check, trace)
        active_step = "answer"
        _answer_from_new_claims(topic, response, emit, check)
        return response
    except BaseException:
        emit(active_step, "failed", "This stage could not be completed.")
        raise
