"""Portable references to retained source snapshots, and their extracted text.

Snapshots are content-addressed and never rewritten, so their extracted text
can be cached (in memory and in a `.txt` sidecar) for as long as the file is
unchanged. Re-verification then reads the text instead of re-parsing PDFs.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path

from . import config

_TEXT_CACHE_MAX_CHARS = 32_000_000
_text_cache: OrderedDict[tuple[str, int, int], str] = OrderedDict()
_text_cache_chars = 0
_hash_cache: dict[tuple[str, int, int], str] = {}
_cache_lock = threading.Lock()


def metadata_path(path: Path) -> str:
    """Encode a local snapshot without leaking the server's absolute path."""
    root = config.DATA_DIR.resolve()
    target = path.resolve()
    if not target.is_relative_to(root):
        raise ValueError("Source snapshot must be inside SPYMONK_DATA_DIR")
    return target.relative_to(root).as_posix()


def portable_path(path: Path) -> str:
    """Relative reference for files inside DATA_DIR; empty for files outside it."""
    try:
        return metadata_path(path)
    except (OSError, ValueError):
        return ""


def resolve_snapshot_path(value: str) -> Path | None:
    """Accept relative references and safe legacy absolute references only."""
    if not value:
        return None
    try:
        root = config.DATA_DIR.resolve()
        supplied = Path(value)
        target = (supplied if supplied.is_absolute() else root / supplied).resolve()
        return target if target.is_relative_to(root) else None
    except (OSError, RuntimeError, ValueError):
        return None


def write_bytes_atomic(path: Path, body: bytes) -> None:
    """Write a complete file under a temporary name, then move it into place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as output:
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _file_key(path: Path) -> tuple[str, int, int]:
    stat = path.stat()
    return str(path.resolve()), stat.st_size, stat.st_mtime_ns


def file_sha256(path: Path) -> str:
    key = _file_key(path)
    with _cache_lock:
        cached = _hash_cache.get(key)
    if cached is not None:
        return cached
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    value = digest.hexdigest()
    with _cache_lock:
        _hash_cache[key] = value
    return value


def sidecar_path(path: Path) -> Path:
    return path.with_name(path.name + ".txt")


def _remember(key: tuple[str, int, int], text: str) -> None:
    global _text_cache_chars
    with _cache_lock:
        previous = _text_cache.pop(key, None)
        if previous is not None:
            _text_cache_chars -= len(previous)
        _text_cache[key] = text
        _text_cache_chars += len(text)
        while _text_cache_chars > _TEXT_CACHE_MAX_CHARS and len(_text_cache) > 1:
            _, evicted = _text_cache.popitem(last=False)
            _text_cache_chars -= len(evicted)


def remember_snapshot_text(path: Path, text: str) -> None:
    """Store text already extracted from a snapshot (best effort)."""
    try:
        key = _file_key(path)
        write_bytes_atomic(sidecar_path(path), text.encode("utf-8"))
    except OSError:
        return
    _remember(key, text)


def snapshot_text(path: Path, extract: Callable[[Path], str]) -> str:
    """Return a snapshot's text from memory, its sidecar, or by extracting it once."""
    key = _file_key(path)
    with _cache_lock:
        cached = _text_cache.get(key)
        if cached is not None:
            _text_cache.move_to_end(key)
            return cached

    text: str | None = None
    sidecar = sidecar_path(path)
    try:
        if sidecar.exists() and sidecar.stat().st_mtime_ns >= key[2]:
            text = sidecar.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        text = None
    if text is None:
        text = extract(path)
        try:
            write_bytes_atomic(sidecar, text.encode("utf-8"))
        except OSError:
            pass
    _remember(key, text)
    return text


def clear_snapshot_caches() -> None:
    global _text_cache_chars
    with _cache_lock:
        _text_cache.clear()
        _hash_cache.clear()
        _text_cache_chars = 0
