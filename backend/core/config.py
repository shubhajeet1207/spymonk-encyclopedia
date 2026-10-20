"""Shared paths and environment-backed settings for the encyclopedia.

Settings are read once at import and validated here, so a typo in `.env` fails
at startup with the variable name instead of deep inside a request.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
# SPYMONK_SKIP_DOTENV=1 ignores .env, so tools that must use the defaults
# (the contract check) are not affected by local settings.
if os.getenv("SPYMONK_SKIP_DOTENV") != "1":
    load_dotenv(ROOT_DIR / ".env")


def _env_str(name: str, default: str) -> str:
    return (os.getenv(name) or default).strip()


def _env_choice(name: str, default: str, choices: set[str]) -> str:
    value = _env_str(name, default).lower()
    if value not in choices:
        raise ValueError(f"{name} must be one of: {', '.join(sorted(choices))}.")
    return value


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.getenv(name)
    try:
        value = int(raw) if raw not in (None, "") else default
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}.")
    return value


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.getenv(name)
    try:
        value = float(raw) if raw not in (None, "") else default
    except ValueError as exc:
        raise ValueError(f"{name} must be a number.") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}.")
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false.")


DATA_DIR = Path(os.getenv("SPYMONK_DATA_DIR", str(ROOT_DIR))).expanduser().resolve()
PDF_DIR = DATA_DIR / "data" / "pdfs"
WEB_PDF_DIR = DATA_DIR / "data" / "web_pdfs"
WEB_HTML_DIR = DATA_DIR / "data" / "web_html"
MEMORY_DIR = DATA_DIR / "memory" / "topics"
CHROMA_DIR = DATA_DIR / "chroma_db"
COLLECTION_NAME = "personal_encyclopedia"

VECTOR_STORE_PROVIDER = _env_choice(
    "VECTOR_STORE_PROVIDER", "chroma-local", {"chroma-local", "chroma-cloud"}
)
CHROMA_API_KEY = _env_str("CHROMA_API_KEY", "")
CHROMA_TENANT = _env_str("CHROMA_TENANT", "")
CHROMA_DATABASE = _env_str("CHROMA_DATABASE", "")
# Hugging Face Spaces sets SPACE_ID and SPACE_HOST (for example
# "name-space.hf.space"); SPYMONK_HOSTED=true marks any other hosted deployment.
SPACE_HOST = _env_str("SPACE_HOST", "").lower()
HOSTED = _env_bool("SPYMONK_HOSTED", bool(_env_str("SPACE_ID", "")))
ALLOWED_ORIGINS = tuple(
    origin.strip().rstrip("/")
    for origin in os.getenv(
        "APP_ALLOWED_ORIGINS",
        f"https://{SPACE_HOST}" if SPACE_HOST else "http://127.0.0.1:5173,http://localhost:5173",
    ).split(",")
    if origin.strip()
)
ALLOWED_HOSTS = tuple(
    host.strip().lower()
    for host in os.getenv(
        "APP_ALLOWED_HOSTS", f"{SPACE_HOST},127.0.0.1,localhost" if SPACE_HOST else "127.0.0.1,localhost"
    ).split(",")
    if host.strip()
)
APP_API_TOKEN = _env_str("APP_API_TOKEN", "")
# One API process owns DATA_DIR; a second process refuses to start.
INSTANCE_LOCK = _env_bool("SPYMONK_INSTANCE_LOCK", True)
# Interactive API docs are for local development; hosted deployments hide them.
API_DOCS = _env_bool("APP_API_DOCS", not HOSTED)
# Who may embed the UI in a frame. A Space page shows its app in a
# huggingface.co frame; everywhere else nobody may.
FRAME_ANCESTORS = _env_str("APP_FRAME_ANCESTORS", "https://huggingface.co" if SPACE_HOST else "'none'")
if not re.fullmatch(r"[A-Za-z0-9:/.*' -]+", FRAME_ANCESTORS):
    raise ValueError("APP_FRAME_ANCESTORS may contain only origins, 'self', or 'none'.")

# Durable backup of the data directory, for hosts whose disk is wiped on
# restart: "hf-dataset" (a private Hugging Face dataset) or "directory".
BACKUP_TARGET = _env_choice("BACKUP_TARGET", "", {"", "hf-dataset", "directory"})
BACKUP_HF_REPO = _env_str("BACKUP_HF_REPO", "")
BACKUP_DIRECTORY = _env_str("BACKUP_DIRECTORY", "")
BACKUP_INTERVAL_SECONDS = _env_float("BACKUP_INTERVAL_SECONDS", 15.0, minimum=2.0)
BACKUP_ALLOW_PUBLIC = _env_bool("BACKUP_ALLOW_PUBLIC", False)
# Named apart from HF_TOKEN, which the Hugging Face tools use for your own login.
BACKUP_HF_TOKEN = _env_str("BACKUP_HF_TOKEN", "")
if BACKUP_TARGET == "hf-dataset" and not BACKUP_HF_REPO:
    raise ValueError("BACKUP_HF_REPO must name the dataset, for example your-name/spymonk-data.")
if BACKUP_TARGET == "directory" and not BACKUP_DIRECTORY:
    raise ValueError("BACKUP_DIRECTORY must be set when BACKUP_TARGET=directory.")
# Rebuild a missing index from audits and PDFs at startup (restored backups
# bring files, not the index).
REBUILD_INDEX_ON_START = _env_bool("REBUILD_INDEX_ON_START", HOSTED)

# Chunking used with remote (tiktoken-measured) embedding models.
CHUNK_SIZE_TOKENS = 500
CHUNK_OVERLAP_TOKENS = 50
RETRIEVE_K = 5
# Retrieval over-fetches so duplicates and weak matches can be dropped.
RETRIEVE_FETCH_K = 12
MIN_QUOTE_CHARS = 60
MAX_WEB_PDFS = 4
MAX_WEB_HTML = 6
MAX_CANDIDATE_CLAIMS_PER_TOPIC = 12
MAX_CLAIMS_PER_TOPIC = 20
MAX_CROSS_CHECKS_PER_CLAIM = 5
CROSS_CHECK_BATCH_SIZE = 6
MIN_INDEPENDENT_ORIGINS = 2
SOURCE_TEXT_FOR_ANALYSIS_CHARS = 45_000
MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_HTML_BYTES = 2 * 1024 * 1024
FETCH_TIMEOUT_SECONDS = 25
FETCH_CONCURRENCY = _env_int("FETCH_CONCURRENCY", 4, minimum=1)
FETCH_PER_HOST_CONCURRENCY = 2
RESEARCH_MAX_FETCH_BYTES = _env_int("RESEARCH_MAX_FETCH_BYTES", 80 * 1024 * 1024, minimum=1)
USER_AGENT = "SpyMonkEncyclopedia/1.0 (+personal research; not for bulk crawling)"

# Research jobs, rate limits, and spend ceilings.
RESEARCH_MAX_SECONDS = _env_int("RESEARCH_MAX_SECONDS", 900, minimum=30)
RESEARCH_MAX_CONCURRENT_JOBS = _env_int("RESEARCH_MAX_CONCURRENT_JOBS", 1, minimum=1)
RESEARCH_MAX_QUEUED_JOBS = _env_int("RESEARCH_MAX_QUEUED_JOBS", 4, minimum=0)
RATE_LIMIT_RESEARCH_PER_MINUTE = _env_int("RATE_LIMIT_RESEARCH_PER_MINUTE", 6, minimum=1)
RATE_LIMIT_ASK_PER_MINUTE = _env_int("RATE_LIMIT_ASK_PER_MINUTE", 30, minimum=1)
RATE_LIMIT_UPLOAD_PER_MINUTE = _env_int("RATE_LIMIT_UPLOAD_PER_MINUTE", 10, minimum=1)
RATE_LIMIT_MAINTENANCE_PER_MINUTE = _env_int("RATE_LIMIT_MAINTENANCE_PER_MINUTE", 3, minimum=1)
# 0 disables the ceiling.
LLM_DAILY_CALL_LIMIT = _env_int("LLM_DAILY_CALL_LIMIT", 2000, minimum=0)
LLM_CONCURRENCY = _env_int("LLM_CONCURRENCY", 3, minimum=1)

_explicit_llm_base_url = (os.getenv("LLM_BASE_URL") or "").strip()
_legacy_openai_base_url = (os.getenv("OPENAI_BASE_URL") or "").strip()
LLM_BASE_URL = _explicit_llm_base_url or _legacy_openai_base_url or None
# A globally configured OpenAI key must never be forwarded to a custom LLM
# endpoint merely because the project-specific key was left unset.
LLM_API_KEY = (
    os.getenv("LLM_API_KEY")
    or os.getenv("NVIDIA_API_KEY")
    or (os.getenv("OPENAI_API_KEY") if not LLM_BASE_URL else None)
)
LLM_MODEL = _env_str("LLM_MODEL", "deepseek-ai/deepseek-v4.1-flash")
LLM_TEMPERATURE = _env_float("LLM_TEMPERATURE", 0.2)
LLM_TOP_P = _env_float("LLM_TOP_P", 0.95)
LLM_MAX_TOKENS = _env_int("LLM_MAX_TOKENS", 2048, minimum=1)
LLM_DISABLE_THINKING = _env_bool("LLM_DISABLE_THINKING", False)
LLM_TIMEOUT_SECONDS = _env_float("LLM_TIMEOUT_SECONDS", 60.0, minimum=1.0)
LLM_MAX_RETRIES = _env_int("LLM_MAX_RETRIES", 1, minimum=0)

EMBEDDING_PROVIDER = _env_choice("EMBEDDING_PROVIDER", "huggingface", {"huggingface", "openai", "llm"})
EMBEDDING_MODEL = _env_str("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
OPENAI_EMBEDDING_MODEL = _env_str("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_BASE_URL = (os.getenv("EMBEDDING_BASE_URL") or "").strip() or None
EMBEDDING_API_KEY = (os.getenv("EMBEDDING_API_KEY") or "").strip() or (
    (os.getenv("OPENAI_API_KEY") or "").strip() if not EMBEDDING_BASE_URL else ""
)
# Longest input the local embedding model reads; longer chunks are truncated
# by the model, so local chunks are sized below this (all-MiniLM-L6-v2: 256).
EMBEDDING_MAX_TOKENS = _env_int("EMBEDDING_MAX_TOKENS", 256, minimum=64)
EMBEDDINGS_WARMUP = _env_bool("EMBEDDINGS_WARMUP", False)
# Cosine similarity below which a retrieved passage is treated as unrelated.
# Calibrated on all-MiniLM-L6-v2 (related >= 0.44, unrelated <= 0.15).
RETRIEVE_MIN_SIMILARITY = _env_float(
    "RETRIEVE_MIN_SIMILARITY", 0.25 if EMBEDDING_PROVIDER == "huggingface" else 0.2
)

# Directory with TrueType fonts for PDF reports (NotoSans-Regular.ttf,
# NotoSans-Bold.ttf, NotoSansDevanagari-Regular.ttf); system fonts otherwise.
REPORT_FONT_DIR = _env_str("REPORT_FONT_DIR", "")

# Logs live with the data: the API log (rotated) and one developer log per
# research run under research/, of which the newest RESEARCH_LOG_KEEP are kept.
LOG_DIR = Path(_env_str("SPYMONK_LOG_DIR", "") or DATA_DIR / "logs").expanduser().resolve()
LOG_TO_FILE = _env_bool("LOG_TO_FILE", True)
LOG_FILE_MAX_BYTES = _env_int("LOG_FILE_MAX_BYTES", 5 * 1024 * 1024, minimum=64 * 1024)
LOG_FILE_BACKUPS = _env_int("LOG_FILE_BACKUPS", 5, minimum=1)
RESEARCH_LOG_KEEP = _env_int("RESEARCH_LOG_KEEP", 500, minimum=0)


def require_llm_api_key() -> str:
    if not LLM_API_KEY:
        raise RuntimeError(
            "Missing LLM API key. Set LLM_API_KEY or NVIDIA_API_KEY in your environment or .env file."
        )
    return LLM_API_KEY


def require_embedding_api_key() -> str:
    if not EMBEDDING_API_KEY:
        raise RuntimeError(
            "Missing embedding API key. Set EMBEDDING_API_KEY for remote embeddings, "
            "or OPENAI_API_KEY when using the default OpenAI endpoint."
        )
    return EMBEDDING_API_KEY
