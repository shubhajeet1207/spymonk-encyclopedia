"""Chroma access shared by PDF ingest, web research, retrieval, and maintenance.

Local development uses a persistent on-disk Chroma client. Deployed instances
can use Chroma Cloud so vector memory survives API process restarts and deploys.

One client per configured store is reused for the life of the process. Every
collection records the embedding configuration that built it (its
"fingerprint"), so vectors from different embedding models are never mixed.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from backend.observability.logging import safe_exception_context

from . import config
from .evidence import ANSWERABLE_WEB_STATUSES

if TYPE_CHECKING:
    from langchain_chroma import Chroma
    from langchain_core.documents import Document

logger = logging.getLogger(__name__)

INDEX_SCHEMA_VERSION = "2"
REBUILD_SUFFIX = "__rebuild"
_WRITE_BATCH = 256
_PAGE_SIZE = 500
_CLOUD_EXISTS_TTL_SECONDS = 30.0

# Serializes index writes in this process: research saves, uploads, clears,
# and rebuilds. Reads do not take it.
write_lock = threading.RLock()

_clients: dict[tuple, tuple[Any, Any]] = {}
_vectorstores: dict[tuple, Any] = {}
_cloud_exists_cache: dict[tuple, tuple[float, bool]] = {}
_cache_lock = threading.Lock()


class IndexCompatibilityError(RuntimeError):
    """The index was built with a different embedding configuration."""


def _validate_provider() -> None:
    if config.VECTOR_STORE_PROVIDER not in {"chroma-local", "chroma-cloud"}:
        raise ValueError(
            "VECTOR_STORE_PROVIDER must be 'chroma-local' or 'chroma-cloud'."
        )


def _is_cloud() -> bool:
    return config.VECTOR_STORE_PROVIDER == "chroma-cloud"


def _store_key() -> tuple:
    if _is_cloud():
        return ("chroma-cloud", config.CHROMA_TENANT, config.CHROMA_DATABASE)
    return ("chroma-local", str(config.CHROMA_DIR))


def _cloud_client():
    missing = [
        name
        for name, value in (
            ("CHROMA_API_KEY", config.CHROMA_API_KEY),
            ("CHROMA_TENANT", config.CHROMA_TENANT),
            ("CHROMA_DATABASE", config.CHROMA_DATABASE),
        )
        if not value
    ]
    if missing:
        logger.error(
            "Chroma Cloud configuration is incomplete (missing=%s)",
            ",".join(missing),
        )
        raise RuntimeError(
            "Chroma Cloud is selected but missing environment variable(s): "
            + ", ".join(missing)
        )

    import chromadb

    return chromadb.CloudClient(
        tenant=config.CHROMA_TENANT,
        database=config.CHROMA_DATABASE,
        api_key=config.CHROMA_API_KEY,
    )


def _client():
    """Return this process's client for the configured store, creating it once."""
    _validate_provider()
    import chromadb

    factory = chromadb.CloudClient if _is_cloud() else chromadb.PersistentClient
    key = _store_key()
    with _cache_lock:
        cached = _clients.get(key)
        # A replaced factory (tests patch chromadb) must not reuse a stale client.
        if cached is not None and cached[0] is factory:
            return cached[1]
    if _is_cloud():
        client = _cloud_client()
    else:
        config.CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
    with _cache_lock:
        _clients[key] = (factory, client)
        for vector_key in [k for k in _vectorstores if k[0] == key]:
            _vectorstores.pop(vector_key, None)
    return client


def _invalidate() -> None:
    """Drop cached wrappers after the collection was replaced or emptied."""
    key = _store_key()
    with _cache_lock:
        for vector_key in [k for k in _vectorstores if k[0] == key]:
            _vectorstores.pop(vector_key, None)
        _cloud_exists_cache.pop(key, None)


def close_clients() -> None:
    """Release every cached client (app shutdown and test cleanup)."""
    with _cache_lock:
        clients = [client for _, client in _clients.values()]
        _clients.clear()
        _vectorstores.clear()
        _cloud_exists_cache.clear()
    for client in clients:
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception as exc:  # pragma: no cover - best-effort shutdown
                logger.warning("Vector store client close failed (%s)", safe_exception_context(exc))


def index_fingerprint() -> dict[str, str]:
    from .embeddings import embedding_identity
    from .ingest import chunker_version

    provider, model = embedding_identity()
    return {
        "spymonk_schema": INDEX_SCHEMA_VERSION,
        "spymonk_embedding_provider": provider,
        "spymonk_embedding_model": model,
        "spymonk_chunker": chunker_version(),
    }


def _compatibility(metadata: dict | None) -> str:
    """ok, legacy (no fingerprint), stale (other chunking), or mismatch (other model)."""
    recorded = metadata or {}
    if "spymonk_embedding_model" not in recorded:
        return "legacy"
    current = index_fingerprint()
    if (
        recorded.get("spymonk_embedding_provider") != current["spymonk_embedding_provider"]
        or recorded.get("spymonk_embedding_model") != current["spymonk_embedding_model"]
    ):
        return "mismatch"
    if (
        recorded.get("spymonk_chunker") != current["spymonk_chunker"]
        or recorded.get("spymonk_schema") != current["spymonk_schema"]
    ):
        return "stale"
    return "ok"


def _mismatch_message(metadata: dict | None) -> str:
    recorded = metadata or {}
    current = index_fingerprint()
    return (
        "The searchable index was built with embedding model "
        f"{recorded.get('spymonk_embedding_model')} ({recorded.get('spymonk_embedding_provider')}), "
        f"but the current configuration uses {current['spymonk_embedding_model']} "
        f"({current['spymonk_embedding_provider']}). Rebuild the index with "
        "`python -m backend.core.reindex --apply` or the Rebuild index button."
    )


def _get_collection(name: str | None = None):
    from chromadb.errors import NotFoundError

    try:
        return _client().get_collection(name=name or config.COLLECTION_NAME, embedding_function=None)
    except NotFoundError:
        return None


def _create_collection(name: str | None = None):
    # get_or_create keeps the existing metadata when the collection exists, so
    # the fingerprint is recorded only for collections created here.
    return _client().get_or_create_collection(
        name=name or config.COLLECTION_NAME,
        metadata=index_fingerprint(),
        embedding_function=None,
    )


def _delete_collection(name: str) -> None:
    from chromadb.errors import NotFoundError

    try:
        _client().delete_collection(name=name)
    except NotFoundError:
        pass


def _local_index_missing() -> bool:
    return not _is_cloud() and (
        not config.CHROMA_DIR.exists() or not any(config.CHROMA_DIR.iterdir())
    )


def index_exists() -> bool:
    _validate_provider()
    if _local_index_missing():
        return False
    if _is_cloud():
        key = _store_key()
        with _cache_lock:
            cached = _cloud_exists_cache.get(key)
        if cached is not None and time.monotonic() - cached[0] < _CLOUD_EXISTS_TTL_SECONDS:
            return cached[1]
    collection = _get_collection()
    exists = bool(collection is not None and collection.count() > 0)
    if _is_cloud():
        with _cache_lock:
            _cloud_exists_cache[_store_key()] = (time.monotonic(), exists)
    return exists


def index_health() -> dict[str, object]:
    """Summarize whether the index matches the current embedding configuration."""
    _validate_provider()
    if _local_index_missing():
        return {"state": "missing", "rebuild_recommended": False, "message": ""}
    collection = _get_collection()
    if collection is None or collection.count() == 0:
        return {"state": "missing", "rebuild_recommended": False, "message": ""}
    state = _compatibility(collection.metadata)
    messages = {
        "ok": "",
        "legacy": "The index predates embedding fingerprints and the current chunk sizes; rebuild it once.",
        "stale": "The index was chunked with older settings; rebuild it for better search results.",
        "mismatch": _mismatch_message(collection.metadata),
    }
    return {"state": state, "rebuild_recommended": state != "ok", "message": messages[state]}


def _require_compatible(collection) -> None:
    if collection is not None and _compatibility(collection.metadata) == "mismatch":
        raise IndexCompatibilityError(_mismatch_message(collection.metadata))


def _vectorstore(name: str | None = None) -> Chroma:
    from langchain_chroma import Chroma

    from .embeddings import get_embeddings

    collection_name = name or config.COLLECTION_NAME
    embeddings = get_embeddings()
    client = _client()
    key = (_store_key(), collection_name, id(embeddings), id(client))
    with _cache_lock:
        cached = _vectorstores.get(key)
    if cached is not None:
        return cached
    store = Chroma(client=client, collection_name=collection_name, embedding_function=embeddings)
    with _cache_lock:
        _vectorstores[key] = store
    return store


def similarity_converter():
    """Return a function mapping this collection's distances to cosine similarity.

    Embeddings are normalized, so every supported distance space has an exact
    cosine equivalent; this lets one similarity threshold work for all of them.
    """
    space = "l2"
    collection = _get_collection()
    configuration = getattr(collection, "configuration_json", None) or {}
    hnsw = configuration.get("hnsw") if isinstance(configuration, dict) else None
    if isinstance(hnsw, dict) and hnsw.get("space"):
        space = str(hnsw["space"])
    if space == "l2":
        # Chroma reports squared L2: |a-b|^2 = 2 - 2cos for unit vectors.
        return lambda distance: 1.0 - float(distance) / 2.0
    return lambda distance: 1.0 - float(distance)


def document_id(document: Document) -> str:
    metadata = document.metadata
    if metadata.get("claim_id"):
        payload = f"claim|{metadata['claim_id']}"
    elif metadata.get("document_sha256") and metadata.get("chunk_index") not in (None, ""):
        payload = (
            f"pdf|{metadata['document_sha256']}|{metadata.get('chunker', '')}|{metadata['chunk_index']}"
        )
    else:
        payload = "|".join(
            [
                document.page_content,
                str(metadata.get("source", "")),
                str(metadata.get("source_url", "")),
                str(metadata.get("chunk_index", "")),
                "",
            ]
        )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _unique_by_id(documents: Iterable[Document]) -> tuple[list[Document], list[str]]:
    unique: dict[str, Document] = {}
    for document in documents:
        unique.setdefault(document_id(document), document)
    return list(unique.values()), list(unique.keys())


def load_vectorstore() -> Chroma:
    if not index_exists():
        raise FileNotFoundError(
            "No encyclopedia memory yet. Research a topic from the web or ingest a PDF first."
        )
    _require_compatible(_get_collection())
    return _vectorstore()


def _topic_where(topic: str) -> dict[str, Any]:
    from .memory import canonical_topic

    # Records written before topic keys existed only carry the raw topic.
    return {"$or": [{"topic_key": canonical_topic(topic)}, {"topic": topic}]}


def find_topic_documents(topic: str, *, limit: int = config.MAX_CLAIMS_PER_TOPIC) -> list[Document]:
    """Read verified claims for a saved topic without loading embeddings."""
    _validate_provider()
    if not _is_cloud() and not index_exists():
        return []
    collection = _get_collection()
    if collection is None:
        return []
    result = collection.get(
        where={
            "$and": [
                {"origin": "web"},
                {"validation_status": {"$in": sorted(ANSWERABLE_WEB_STATUSES)}},
                _topic_where(topic),
            ]
        },
        limit=limit,
        include=["documents", "metadatas"],
    )
    from langchain_core.documents import Document

    return [
        Document(page_content=content, metadata=metadata or {})
        for content, metadata in zip(result["documents"] or [], result["metadatas"] or [])
        if content
        and metadata
        and metadata.get("validation_status") in ANSWERABLE_WEB_STATUSES
    ]


def _scan_metadatas(where: dict[str, Any]) -> Iterable[dict]:
    collection = _get_collection()
    if collection is None:
        return
    offset = 0
    while True:
        result = collection.get(where=where, include=["metadatas"], limit=_PAGE_SIZE, offset=offset)
        metadatas = result.get("metadatas") or []
        for metadata in metadatas:
            if isinstance(metadata, dict):
                yield metadata
        count = len(result.get("ids") or [])
        if count < _PAGE_SIZE:
            return
        offset += count


def indexed_pdf_count() -> int:
    """Count unique uploaded PDFs represented by local-PDF vectors."""
    _validate_provider()
    if _local_index_missing():
        return 0
    sources: set[str] = set()
    for metadata in _scan_metadatas({"origin": "local_pdf"}):
        source = (
            metadata.get("document_sha256")
            or metadata.get("source_path")
            or metadata.get("local_path")
            or metadata.get("source")
        )
        if isinstance(source, str) and source:
            sources.add(source)
    return len(sources)


def indexed_document_hashes(hashes: Iterable[str]) -> set[str]:
    """Which of these PDF content hashes are already in the index."""
    wanted = sorted({value for value in hashes if value})
    if not wanted or not index_exists():
        return set()
    found: set[str] = set()
    for metadata in _scan_metadatas({"document_sha256": {"$in": wanted}}):
        value = metadata.get("document_sha256")
        if isinstance(value, str):
            found.add(value)
    return found


def indexed_claim_counts() -> dict[str, int]:
    """Answerable web claims per canonical topic key."""
    from .memory import canonical_topic

    if not index_exists():
        return {}
    counts: dict[str, int] = {}
    where = {
        "$and": [
            {"origin": "web"},
            {"validation_status": {"$in": sorted(ANSWERABLE_WEB_STATUSES)}},
        ]
    }
    for metadata in _scan_metadatas(where):
        key = metadata.get("topic_key") or canonical_topic(str(metadata.get("topic") or ""))
        if key:
            counts[key] = counts.get(key, 0) + 1
    return counts


def clear_vectorstore() -> None:
    _validate_provider()
    with write_lock:
        logger.info(
            "Clearing %s vector collection", "Chroma Cloud" if _is_cloud() else "local Chroma"
        )
        # Deleting the collection (not the database files) works while this
        # process holds the SQLite file open, including on Windows.
        _delete_collection(config.COLLECTION_NAME)
        _create_collection()
        _invalidate()


def _add_documents(collection_name: str, documents: list[Document], ids: list[str]) -> None:
    store = _vectorstore(collection_name)
    for start in range(0, len(documents), _WRITE_BATCH):
        store.add_documents(
            documents=documents[start : start + _WRITE_BATCH],
            ids=ids[start : start + _WRITE_BATCH],
        )


def upsert_documents(documents: list[Document], *, reset: bool = False) -> int:
    _validate_provider()
    if not documents:
        raise ValueError("No documents to index.")
    documents, ids = _unique_by_id(documents)
    logger.info(
        "Indexing documents (document_count=%s provider=%s reset=%s)",
        len(documents),
        config.VECTOR_STORE_PROVIDER,
        reset,
    )
    with write_lock:
        if reset:
            clear_vectorstore()
        collection = _get_collection() or _create_collection()
        state = _compatibility(collection.metadata)
        if state == "mismatch":
            raise IndexCompatibilityError(_mismatch_message(collection.metadata))
        if state == "legacy" and collection.count() == 0:
            # An empty unfingerprinted collection holds no vectors to conflict with.
            collection.modify(metadata=index_fingerprint())
            _invalidate()
        # Deterministic IDs make add_documents an upsert, so re-ingestion is safe.
        _add_documents(config.COLLECTION_NAME, documents, ids)
        _invalidate()
    logger.info("Document indexing completed (document_count=%s)", len(documents))
    return len(documents)


def replace_topic_web_documents(
    topic: str, documents: list[Document], *, remove_stale: bool = True
) -> int:
    """Replace indexed web claims for one topic while keeping local PDFs intact.

    With remove_stale=False the topic's other claims stay indexed; a degraded
    research run uses this so it can add claims without deleting any.
    """
    if not remove_stale:
        if documents:
            upsert_documents(documents, reset=False)
        return len(documents)
    with write_lock:
        existing_ids: set[str] = set()
        if index_exists():
            collection = _get_collection()
            if collection is not None:
                existing = collection.get(
                    where={"$and": [{"origin": "web"}, _topic_where(topic)]},
                    include=[],
                )
                existing_ids = set(existing.get("ids") or [])

        if documents:
            upsert_documents(documents, reset=False)
        new_ids = {document_id(document) for document in documents}
        stale_ids = existing_ids - new_ids
        if stale_ids:
            collection = _get_collection()
            if collection is not None:
                collection.delete(ids=sorted(stale_ids))
            _invalidate()
        return len(documents)


def rebuild_collection(documents: list[Document]) -> int:
    """Build a fresh, fingerprinted collection, then swap it in for the live one.

    The live index keeps serving until the new collection is complete; a failed
    build leaves it untouched.
    """
    _validate_provider()
    documents, ids = _unique_by_id(documents)
    temporary = config.COLLECTION_NAME + REBUILD_SUFFIX
    with write_lock:
        _delete_collection(temporary)
        _invalidate()
        collection = _client().create_collection(
            name=temporary, metadata=index_fingerprint(), embedding_function=None
        )
        try:
            if documents:
                _add_documents(temporary, documents, ids)
        except BaseException:
            _delete_collection(temporary)
            _invalidate()
            raise
        _delete_collection(config.COLLECTION_NAME)
        collection.modify(name=config.COLLECTION_NAME)
        with _cache_lock:
            _vectorstores.clear()
        _invalidate()
    logger.info("Vector index rebuilt (document_count=%s)", len(documents))
    return len(documents)
