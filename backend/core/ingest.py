"""Load a local PDF, chunk by tokens, embed, and persist to ChromaDB."""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter, TextSplitter

from . import config
from .snapshot_paths import file_sha256, portable_path
from .store import upsert_documents

logger = logging.getLogger(__name__)

_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]
# Room for the [CLS]/[SEP] tokens the embedding model adds to every chunk.
_SPECIAL_TOKEN_MARGIN = 32

# A fresh process bounds the lifetime of pypdf, including pathological PDFs
# that never return from parsing. Keep these separate from the HTTP upload-byte
# limit: a small compressed PDF can expand to far more text in memory.
PDF_PARSE_TIMEOUT_SECONDS = 45
MAX_PDF_PAGES = 250
MAX_PDF_TEXT_BYTES = 4 * 1024 * 1024
MAX_PDF_PAGE_TEXT_BYTES = 512 * 1024
_MAX_PARSER_OUTPUT_BYTES = MAX_PDF_TEXT_BYTES * 6 + MAX_PDF_PAGES * 40 + 4096

# The child imports only pypdf, not this module (whose config loads .env).
# Its output contains page numbers and bounded text, never PDF metadata or
# exception messages controlled by the document. stderr is discarded because
# pypdf can emit details from an untrusted PDF there.
_PDF_PARSER_PROGRAM = r"""
import json
import sys
from pypdf import PdfReader

path = sys.argv[1]
page_limit = int(sys.argv[2])
total_limit = int(sys.argv[3])
per_page_limit = int(sys.argv[4])
try:
    reader = PdfReader(path, strict=False)
    if len(reader.pages) > page_limit:
        result = {"error": "page_limit"}
    else:
        pages = []
        total_bytes = 0
        for number, page in enumerate(reader.pages):
            content = page.extract_text() or ""
            content_bytes = len(content.encode("utf-8", errors="replace"))
            total_bytes += content_bytes
            if content_bytes > per_page_limit or total_bytes > total_limit:
                result = {"error": "text_limit"}
                break
            pages.append([number, content])
        else:
            result = {"pages": pages}
except Exception as exc:
    result = {"error": "parse_failed", "type": type(exc).__name__}
sys.stdout.buffer.write(json.dumps(result, ensure_ascii=True, separators=(",", ":")).encode("ascii"))
"""

def _chunk_sizes() -> tuple[int, int]:
    if config.EMBEDDING_PROVIDER == "huggingface":
        size = config.EMBEDDING_MAX_TOKENS - _SPECIAL_TOKEN_MARGIN
        return size, max(16, size // 8)
    return config.CHUNK_SIZE_TOKENS, config.CHUNK_OVERLAP_TOKENS


def chunker_version() -> str:
    """Identifies how chunks were cut; part of the index fingerprint."""
    size, overlap = _chunk_sizes()
    if config.EMBEDDING_PROVIDER == "huggingface":
        return f"hf:{config.EMBEDDING_MODEL}:{size}/{overlap}:dedupe-v1"
    return f"tiktoken:cl100k_base:{size}/{overlap}:dedupe-v1"


@lru_cache(maxsize=1)
def get_splitter() -> TextSplitter:
    """Measure chunks in the embedding model's own tokens, built on first use."""
    size, overlap = _chunk_sizes()
    if config.EMBEDDING_PROVIDER == "huggingface":
        try:
            from transformers import AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                "Local embeddings need the optional packages in requirements-local-embeddings.txt, "
                "or set EMBEDDING_PROVIDER=openai."
            ) from exc
        try:
            tokenizer = AutoTokenizer.from_pretrained(config.EMBEDDING_MODEL, local_files_only=True)
        except OSError:
            tokenizer = AutoTokenizer.from_pretrained(config.EMBEDDING_MODEL)
        return RecursiveCharacterTextSplitter.from_huggingface_tokenizer(
            tokenizer, chunk_size=size, chunk_overlap=overlap, separators=_SEPARATORS
        )
    return RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        encoding_name="cl100k_base",
        chunk_size=size,
        chunk_overlap=overlap,
        separators=_SEPARATORS,
    )


class NoExtractableTextError(ValueError):
    """Raised when PDF pages contain no text that can be searched or indexed."""


class PdfParsingError(NoExtractableTextError):
    """Raised when an untrusted PDF cannot be safely parsed for indexing."""


def _parser_environment() -> dict[str, str]:
    """Do not forward API keys or application settings into the PDF worker."""
    allowed = ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE")
    return {name: os.environ[name] for name in allowed if name in os.environ}


def _parse_pdf_isolated(path: Path) -> list[tuple[int, str]]:
    command = [
        sys.executable,
        "-I",
        "-c",
        _PDF_PARSER_PROGRAM,
        str(path),
        str(MAX_PDF_PAGES),
        str(MAX_PDF_TEXT_BYTES),
        str(MAX_PDF_PAGE_TEXT_BYTES),
    ]
    try:
        # A fixed argv (no shell): this interpreter in isolated mode, our own
        # parser program, a staged file path, and numeric limits.
        worker = subprocess.Popen(  # noqa: S603
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_parser_environment(),
        )
    except OSError as exc:
        logger.exception("PDF parser worker could not start")
        raise PdfParsingError("PDF parsing could not start. Please try again.") from exc

    try:
        output, _ = worker.communicate(timeout=PDF_PARSE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        # communicate(timeout=...) alone leaves the worker alive on Windows.
        worker.kill()
        worker.communicate()
        logger.warning("PDF parser worker timed out after %s seconds", PDF_PARSE_TIMEOUT_SECONDS)
        raise PdfParsingError("PDF parsing timed out. Try a smaller or simpler PDF.") from exc

    if worker.returncode != 0:
        logger.warning("PDF parser worker exited with status %s", worker.returncode)
        raise PdfParsingError("PDF parsing failed. Check that the PDF is readable.")
    if not output or len(output) > _MAX_PARSER_OUTPUT_BYTES:
        logger.warning("PDF parser worker returned an invalid output size (%s bytes)", len(output))
        raise PdfParsingError("PDF parsing returned too much text or an invalid result.")
    try:
        result = json.loads(output)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PdfParsingError("PDF parsing returned an invalid result.") from exc
    if not isinstance(result, dict):
        raise PdfParsingError("PDF parsing returned an invalid result.")
    error = result.get("error")
    if error == "page_limit":
        raise PdfParsingError(f"PDF exceeds the {MAX_PDF_PAGES}-page parsing limit.")
    if error == "text_limit":
        raise PdfParsingError("PDF contains too much extracted text to index safely.")
    if error:
        logger.warning("PDF parser rejected an unreadable PDF (type=%s)", result.get("type", "unknown"))
        raise PdfParsingError("PDF parsing failed. Check that the PDF is readable.")
    pages = result.get("pages")
    if not isinstance(pages, list) or len(pages) > MAX_PDF_PAGES:
        raise PdfParsingError("PDF parsing returned an invalid result.")
    parsed_pages: list[tuple[int, str]] = []
    text_bytes = 0
    for item in pages:
        if not isinstance(item, list) or len(item) != 2:
            raise PdfParsingError("PDF parsing returned an invalid result.")
        page_number, content = item
        if not isinstance(page_number, int) or not isinstance(content, str):
            raise PdfParsingError("PDF parsing returned an invalid result.")
        page_bytes = len(content.encode("utf-8", errors="replace"))
        text_bytes += page_bytes
        if page_bytes > MAX_PDF_PAGE_TEXT_BYTES or text_bytes > MAX_PDF_TEXT_BYTES:
            raise PdfParsingError("PDF parsing returned too much text.")
        parsed_pages.append((page_number, content))
    return parsed_pages


def load_pdf(path: Path, *, source_path: Path | None = None) -> list[Document]:
    if not path.exists() or path.suffix.lower() != ".pdf":
        raise FileNotFoundError(f"PDF not found: {path}")
    # A staged upload is parsed at `path`, but every indexed reference must
    # point to the final durable location after promotion. References are
    # relative to SPYMONK_DATA_DIR so the index never records host paths.
    durable_path = source_path or path
    reference = portable_path(durable_path)
    document_sha256 = file_sha256(path)
    pages = [
        Document(page_content=content, metadata={"page": page_number})
        for page_number, content in _parse_pdf_isolated(path)
    ]
    for page in pages:
        page.metadata["source"] = durable_path.name
        page.metadata["source_path"] = reference
        page.metadata["local_path"] = reference
        page.metadata["document_sha256"] = document_sha256
        page.metadata["origin"] = "local_pdf"
        page.metadata["source_type"] = "pdf"
        page.metadata["source_url"] = ""
        page.metadata["verified"] = "true"
    return pages


def chunk_documents(documents: list[Document]) -> list[Document]:
    """Split pages, number chunks per document, and drop repeated chunk text."""
    chunker = chunker_version()
    counters: dict[str, int] = {}
    seen: dict[str, set[str]] = {}
    chunks: list[Document] = []
    for chunk in get_splitter().split_documents(documents):
        text = (chunk.page_content or "").strip()
        if not text:
            continue
        document_key = str(
            chunk.metadata.get("document_sha256") or chunk.metadata.get("source") or ""
        )
        normalized = " ".join(text.split())
        if normalized in seen.setdefault(document_key, set()):
            continue
        seen[document_key].add(normalized)
        chunk.page_content = text
        chunk.metadata["chunk_index"] = counters.get(document_key, 0)
        chunk.metadata["chunker"] = chunker
        counters[document_key] = chunk.metadata["chunk_index"] + 1
        chunks.append(chunk)
    return chunks


def persist_chunks(chunks: list[Document], *, reset: bool) -> int:
    return upsert_documents(chunks, reset=reset)


def prepare_pdf_chunks(
    pdf_paths: list[Path], *, source_paths: list[Path] | None = None
) -> list[Document]:
    """Parse and validate every PDF before any part of a batch is indexed."""
    if not pdf_paths:
        raise FileNotFoundError("Choose at least one PDF to add to memory.")
    if source_paths is not None and len(source_paths) != len(pdf_paths):
        raise ValueError("Each staged PDF needs exactly one final source path.")
    chunks: list[Document] = []
    for index, path in enumerate(pdf_paths):
        durable_path = source_paths[index] if source_paths is not None else path
        file_chunks = chunk_documents(load_pdf(path, source_path=durable_path))
        if not file_chunks:
            raise NoExtractableTextError(
                f"No searchable text was extracted from {durable_path.name}. "
                "The PDF may be scanned or image-only. Run OCR on it or choose a "
                "text-based PDF, then upload it again."
            )
        chunks.extend(file_chunks)
    return chunks


def ingest_paths(pdf_paths: list[Path], *, reset: bool = False) -> int:
    chunks = prepare_pdf_chunks(pdf_paths)
    return persist_chunks(chunks, reset=reset)


def ingest_directory(pdf_dir: Path | None = None, *, reset: bool = False) -> int:
    directory = pdf_dir or config.PDF_DIR
    directory.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(directory.glob("*.pdf"))
    if not pdfs:
        raise FileNotFoundError(f"No PDF files found in {directory}")
    return ingest_paths(pdfs, reset=reset)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Index a custom PDF encyclopedia into a local Chroma vector store."
    )
    parser.add_argument(
        "--pdf",
        type=Path,
        help="Path to a single PDF. Defaults to every PDF in data/pdfs/.",
    )
    parser.add_argument(
        "--pdf-dir",
        type=Path,
        default=config.PDF_DIR,
        help="Directory of PDFs to index when --pdf is omitted.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Rebuild the vector store instead of appending.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.pdf:
            count = ingest_paths([args.pdf], reset=args.reset)
            source = args.pdf
        else:
            count = ingest_directory(args.pdf_dir, reset=args.reset)
            source = args.pdf_dir
    except Exception as exc:
        print(f"Ingestion failed: {exc}", file=sys.stderr)
        return 1

    print(f"Indexed {count} overlapping chunks from {source}")
    print(f"Vector store: {config.CHROMA_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
