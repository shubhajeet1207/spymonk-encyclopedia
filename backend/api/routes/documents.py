"""PDF upload and ingestion endpoint."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from backend.api.errors import internal_error
from backend.api.schemas import DocumentsResponse
from backend.api.security import require_allowed_origin
from backend.observability.logging import safe_exception_context
from backend.services.rate_limit import limit_upload

router = APIRouter(prefix="/api", tags=["documents"])
MAX_UPLOAD_FILES = 10
logger = logging.getLogger(__name__)


def _clean_filename(filename: str) -> str:
    name = (filename or "document.pdf").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")
    return (name or "document.pdf")[:150]


@router.post("/documents", response_model=DocumentsResponse, dependencies=[Depends(limit_upload)])
async def add_documents(request: Request, files: list[UploadFile] = File(...)) -> dict:
    require_allowed_origin(request)
    if not files:
        raise HTTPException(status_code=422, detail="Choose at least one PDF.")
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(
            status_code=413,
            detail=f"Upload no more than {MAX_UPLOAD_FILES} PDFs at a time.",
        )

    from backend.core import config, store
    from backend.core.ingest import NoExtractableTextError, persist_chunks, prepare_pdf_chunks

    logger.info("PDF ingestion started (file_count=%s)", len(files))
    promoted_paths: list[Path] = []
    display_names: list[str] = []
    duplicates: list[str] = []
    # Stored names carry a random prefix; error messages show the uploaded name.
    uploaded_names: dict[str, str] = {}
    indexed = False
    try:
        config.PDF_DIR.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".upload-", dir=config.PDF_DIR) as staging_directory:
            staged_paths: list[Path] = []
            final_paths: list[Path] = []
            digests: list[str] = []
            for upload in files:
                original_name = _clean_filename(upload.filename or "document.pdf")
                if Path(original_name).suffix.lower() != ".pdf":
                    logger.warning("PDF ingestion rejected a file with a non-PDF extension")
                    raise HTTPException(status_code=415, detail=f"{original_name} is not a PDF.")
                content = await upload.read(config.MAX_PDF_BYTES + 1)
                if len(content) > config.MAX_PDF_BYTES:
                    logger.warning(
                        "PDF ingestion rejected an oversized file (size_bytes=%s limit_bytes=%s)",
                        len(content),
                        config.MAX_PDF_BYTES,
                    )
                    max_mb = config.MAX_PDF_BYTES // (1024 * 1024)
                    raise HTTPException(
                        status_code=413,
                        detail=f"{original_name} exceeds the {max_mb} MB per-file limit.",
                    )
                if b"%PDF-" not in content[:1024]:
                    logger.warning("PDF ingestion rejected a file without a readable PDF signature")
                    raise HTTPException(status_code=415, detail=f"{original_name} is not a readable PDF file.")

                digest = hashlib.sha256(content).hexdigest()
                if digest in digests:
                    duplicates.append(original_name)
                    continue
                unique_name = f"{uuid.uuid4().hex}-{original_name}"
                uploaded_names[unique_name] = original_name
                staged = Path(staging_directory) / unique_name
                destination = config.PDF_DIR / unique_name
                await run_in_threadpool(staged.write_bytes, content)
                staged_paths.append(staged)
                final_paths.append(destination)
                display_names.append(original_name)
                digests.append(digest)

            # Identical bytes already in the index are not stored or indexed again.
            already_indexed = await run_in_threadpool(store.indexed_document_hashes, digests)
            if already_indexed:
                keep = [index for index, digest in enumerate(digests) if digest not in already_indexed]
                duplicates.extend(
                    name for name, digest in zip(display_names, digests) if digest in already_indexed
                )
                staged_paths = [staged_paths[index] for index in keep]
                final_paths = [final_paths[index] for index in keep]
                display_names = [display_names[index] for index in keep]
            if duplicates:
                logger.info("PDF ingestion skipped duplicate files (duplicate_count=%s)", len(duplicates))
            if not staged_paths:
                return {
                    "files": [],
                    "file_count": 0,
                    "chunk_count": 0,
                    "index_exists": await run_in_threadpool(store.index_exists),
                    "duplicates": duplicates,
                }

            prepared = await run_in_threadpool(
                prepare_pdf_chunks, staged_paths, source_paths=final_paths
            )
            for staged, destination in zip(staged_paths, final_paths):
                # Hard-link promotion is atomic and fails rather than replacing
                # any file already at the destination path.
                await run_in_threadpool(os.link, staged, destination)
                promoted_paths.append(destination)
            chunks = await run_in_threadpool(persist_chunks, prepared, reset=False)
            indexed = True
    except NoExtractableTextError as exc:
        logger.warning("PDF ingestion extracted no searchable text (file_count=%s)", len(display_names))
        detail = str(exc)
        for unique_name, original_name in uploaded_names.items():
            detail = detail.replace(unique_name, original_name)
        raise HTTPException(status_code=422, detail=detail) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise internal_error(logger, request, "PDF ingestion", exc, status_code=500) from exc
    finally:
        if not indexed:
            for destination in promoted_paths:
                try:
                    await run_in_threadpool(destination.unlink, missing_ok=True)
                except OSError as exc:
                    logger.error(
                        "PDF upload cleanup failed (%s)", safe_exception_context(exc)
                    )
        for upload in files:
            await upload.close()

    logger.info(
        "PDF ingestion completed (file_count=%s chunk_count=%s index_exists=%s)",
        len(display_names),
        chunks,
        indexed,
    )
    return {
        "files": display_names,
        "file_count": len(display_names),
        "chunk_count": chunks,
        "index_exists": indexed,
        "duplicates": duplicates,
    }
