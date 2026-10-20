"""Rebuild the searchable index from the audit files and uploaded PDFs.

Run `python -m backend.core.reindex` for a dry-run summary and add `--apply`
to rebuild. The new index is built beside the live one and swapped in only
when complete, so a failed rebuild leaves the current index untouched.

Use it after changing the embedding model or chunk settings, after clearing
the index, or to remove duplicate PDF chunks left by older versions.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.documents import Document

from backend.observability.logging import safe_exception_context

from . import config, memory, store
from .evidence import claim_document, is_answerable_web_status
from .ingest import NoExtractableTextError, prepare_pdf_chunks
from .snapshot_paths import file_sha256

logger = logging.getLogger(__name__)
ProgressFn = Callable[[str], None]


@dataclass
class ReindexPlan:
    pdfs: list[str] = field(default_factory=list)
    duplicate_pdfs: list[str] = field(default_factory=list)
    topics: int = 0
    answerable_claims: int = 0
    index_state: str = "missing"

    def as_dict(self) -> dict[str, object]:
        return {
            "pdfs": self.pdfs,
            "duplicate_pdfs": self.duplicate_pdfs,
            "topics": self.topics,
            "answerable_claims": self.answerable_claims,
            "index_state": self.index_state,
        }


@dataclass
class ReindexResult:
    indexed_pdfs: list[str] = field(default_factory=list)
    skipped_pdfs: list[dict[str, str]] = field(default_factory=list)
    duplicate_pdfs: list[str] = field(default_factory=list)
    topics: int = 0
    claims: int = 0
    documents: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "indexed_pdfs": self.indexed_pdfs,
            "skipped_pdfs": self.skipped_pdfs,
            "duplicate_pdfs": self.duplicate_pdfs,
            "topics": self.topics,
            "claims": self.claims,
            "documents": self.documents,
        }


def _unique_pdfs() -> tuple[list[Path], list[Path]]:
    """PDFs in upload order, keeping the first copy of identical files."""
    if not config.PDF_DIR.exists():
        return [], []
    files = sorted(config.PDF_DIR.glob("*.pdf"), key=lambda path: (path.stat().st_mtime, path.name))
    seen: set[str] = set()
    unique: list[Path] = []
    duplicates: list[Path] = []
    for path in files:
        digest = file_sha256(path)
        if digest in seen:
            duplicates.append(path)
            continue
        seen.add(digest)
        unique.append(path)
    return unique, duplicates


def _answerable_claims(archive: dict) -> list[dict]:
    return [
        claim
        for claim in archive.get("claims", [])
        if is_answerable_web_status(claim.get("validation_status"))
        and claim.get("claim")
        and claim.get("quote")
    ]


def plan_reindex() -> ReindexPlan:
    unique, duplicates = _unique_pdfs()
    plan = ReindexPlan(
        pdfs=[path.name for path in unique],
        duplicate_pdfs=[path.name for path in duplicates],
        index_state=str(store.index_health()["state"]),
    )
    for archive in memory.iter_topic_archives():
        plan.topics += 1
        plan.answerable_claims += len(_answerable_claims(archive))
    return plan


def run_reindex(*, on_progress: ProgressFn | None = None) -> ReindexResult:
    def report(message: str) -> None:
        logger.info("%s", message)
        if on_progress:
            on_progress(message)

    result = ReindexResult()
    documents: list[Document] = []
    unique, duplicates = _unique_pdfs()
    result.duplicate_pdfs = [path.name for path in duplicates]
    for index, path in enumerate(unique, start=1):
        report(f"Reading PDF {index} of {len(unique)}")
        try:
            documents.extend(prepare_pdf_chunks([path]))
        except NoExtractableTextError as exc:
            result.skipped_pdfs.append({"file": path.name, "reason": str(exc)})
            continue
        except Exception as exc:
            logger.warning("PDF could not be reindexed (%s)", safe_exception_context(exc))
            result.skipped_pdfs.append({"file": path.name, "reason": "The PDF could not be parsed."})
            continue
        result.indexed_pdfs.append(path.name)

    archives = list(memory.iter_topic_archives())
    for archive in archives:
        result.topics += 1
        for claim in _answerable_claims(archive):
            record = dict(claim)
            record.setdefault("topic", archive["topic"])
            documents.append(claim_document(record))
            result.claims += 1

    report(f"Embedding {len(documents)} passages into a new index")
    result.documents = store.rebuild_collection(documents)
    for archive in archives:
        memory.mark_index_state(archive["topic"], "indexed")
    report("Rebuilt index is live")
    return result


def rebuild_if_missing() -> ReindexResult | None:
    """Build the index when it is absent but audits or PDFs exist.

    A host whose disk is wiped on restart gets its files back from the
    backup, not its index; this makes the archive searchable again.
    """
    if store.index_exists():
        return None
    has_archive = next(memory.iter_topic_archives(), None) is not None
    if not has_archive and not any(config.PDF_DIR.glob("*.pdf")):
        return None
    logger.info("No search index found at startup; rebuilding it from saved research and PDFs")
    return run_reindex()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild the SpyMonk vector index from audits and PDFs.")
    parser.add_argument("--apply", action="store_true", help="Rebuild now; default is a dry run.")
    args = parser.parse_args(argv)
    plan = plan_reindex()
    print(f"Current index state: {plan.index_state}")
    print(f"PDFs to index: {len(plan.pdfs)} (identical copies skipped: {len(plan.duplicate_pdfs)})")
    print(f"Topic audits: {plan.topics}; answerable claims: {plan.answerable_claims}")
    if not args.apply:
        print("Dry run only. Re-run with --apply to rebuild the index.")
        return 0
    result = run_reindex(on_progress=print)
    for skipped in result.skipped_pdfs:
        print(f"Skipped {skipped['file']}: {skipped['reason']}")
    print(f"Indexed {result.documents} passages ({len(result.indexed_pdfs)} PDFs, {result.claims} claims).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
