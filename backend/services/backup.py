"""Back up the archive to durable storage, and restore it before serving.

Free hosts such as Hugging Face Spaces give the container a disk that is wiped
on every restart. With a backup target configured, the API:

1. restores the archive at startup, before it serves anything, and refuses to
   start if the restore fails, rather than running with an empty archive;
2. copies new and changed files to the target every BACKUP_INTERVAL_SECONDS,
   and once more at shutdown.

Only the files that hold knowledge are mirrored (audits, unanswered records,
uploaded PDFs, source snapshots, research run logs). The vector index is not:
it is rebuilt from those files at startup. Remote files are never deleted, so
a failed or partial restore can never erase the backup, and a restore never
overwrites a file that already exists locally.
"""

from __future__ import annotations

import fnmatch
import hashlib
import logging
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Protocol

from backend.core import config
from backend.observability.logging import safe_exception_context

logger = logging.getLogger(__name__)

# Paths relative to the data directory. Everything else (index, lock file,
# temporary files, the API log) stays local.
BACKED_UP = (
    "memory/topics/*.json",
    "memory/history/*.json",
    "memory/unanswered/*.json",
    "memory/attempts/*.json",
    "data/pdfs/*.pdf",
    "data/web_html/*",
    "data/web_pdfs/*",
    "logs/research/*.json",
)
_COMMIT_BATCH = 50
_RESTORE_ATTEMPTS = 3


class BackupError(RuntimeError):
    """The backup target could not be prepared or read."""


def is_backed_up(relative: str) -> bool:
    name = relative.rsplit("/", 1)[-1]
    if name.startswith(".") or name.endswith(".tmp"):
        return False
    return any(fnmatch.fnmatchcase(relative, pattern) for pattern in BACKED_UP)


def backed_up_files(root: Path) -> dict[str, Path]:
    """Files under root that belong in the backup, keyed by relative POSIX path."""
    files: dict[str, Path] = {}
    for top in ("memory", "data", "logs"):
        base = root / top
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_file():
                relative = path.relative_to(root).as_posix()
                if is_backed_up(relative):
                    files[relative] = path
    return files


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class BackupTarget(Protocol):
    description: str

    def prepare(self) -> None: ...

    def download(self, destination: Path) -> None: ...

    def upload(self, files: list[tuple[str, Path]], message: str) -> None: ...


class DirectoryTarget:
    """A directory, for example a mounted persistent volume (and tests)."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.description = f"directory {directory.name}"

    def prepare(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)

    def download(self, destination: Path) -> None:
        for relative, path in backed_up_files(self.directory).items():
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)

    def upload(self, files: list[tuple[str, Path]], message: str) -> None:
        for relative, path in files:
            target = self.directory / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


class HfDatasetTarget:
    """A private Hugging Face dataset repository."""

    def __init__(self, repo_id: str, token: str, *, allow_public: bool = False) -> None:
        self.repo_id = repo_id
        self.token = token or None
        self.allow_public = allow_public
        self.description = f"Hugging Face dataset {repo_id}"

    def _api(self):
        from huggingface_hub import HfApi

        return HfApi(token=self.token)

    def prepare(self) -> None:
        from huggingface_hub.errors import RepositoryNotFoundError

        api = self._api()
        try:
            info = api.repo_info(self.repo_id, repo_type="dataset")
        except RepositoryNotFoundError:
            api.create_repo(self.repo_id, repo_type="dataset", private=True, exist_ok=True)
            info = api.repo_info(self.repo_id, repo_type="dataset")
            logger.info("Created a private backup dataset")
        if not getattr(info, "private", False) and not self.allow_public:
            raise BackupError(
                f"The backup dataset {self.repo_id} is public, and the archive holds your questions and "
                "sources. Make it private in its Hugging Face settings (or set BACKUP_ALLOW_PUBLIC=true)."
            )

    def download(self, destination: Path) -> None:
        from huggingface_hub import snapshot_download

        snapshot_download(
            self.repo_id,
            repo_type="dataset",
            local_dir=destination,
            allow_patterns=list(BACKED_UP),
            token=self.token,
        )

    def upload(self, files: list[tuple[str, Path]], message: str) -> None:
        from huggingface_hub import CommitOperationAdd

        api = self._api()
        for start in range(0, len(files), _COMMIT_BATCH):
            batch = files[start : start + _COMMIT_BATCH]
            api.create_commit(
                self.repo_id,
                repo_type="dataset",
                operations=[CommitOperationAdd(path_in_repo=relative, path_or_fileobj=str(path)) for relative, path in batch],
                commit_message=message,
            )


class BackupManager:
    """Restore once at startup; then mirror changed files until shutdown."""

    def __init__(self, target: BackupTarget, root: Path, *, interval: float) -> None:
        self.target = target
        self.root = root
        self.interval = interval
        # Last backed-up state: relative path -> (size, mtime_ns, sha256).
        self._synced: dict[str, tuple[int, int, str]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def restore(self) -> int:
        """Bring back files missing locally; never overwrite one that exists."""
        last_error: Exception | None = None
        for attempt in range(1, _RESTORE_ATTEMPTS + 1):
            try:
                self.target.prepare()
                restored = self._restore_once()
                break
            except BackupError:
                raise
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Backup restore failed (attempt=%s of %s %s)", attempt, _RESTORE_ATTEMPTS, safe_exception_context(exc)
                )
                if attempt < _RESTORE_ATTEMPTS:
                    time.sleep(2**attempt)
        else:
            raise BackupError(
                f"Could not restore the archive from the {self.target.description}; refusing to start with an "
                "empty archive. Check BACKUP_HF_TOKEN and BACKUP_HF_REPO, then restart."
            ) from last_error
        self._remember(backed_up_files(self.root))
        logger.info("Archive restored from backup (restored_files=%s target=%s)", restored, self.target.description)
        return restored

    def _restore_once(self) -> int:
        self.root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.root, prefix=".restore-") as staging:
            staging_root = Path(staging)
            self.target.download(staging_root)
            restored = 0
            for relative, path in backed_up_files(staging_root).items():
                destination = self.root / relative
                if destination.exists():
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), destination)
                restored += 1
        return restored

    def _remember(self, files: dict[str, Path]) -> None:
        for relative, path in files.items():
            stat = path.stat()
            self._synced[relative] = (stat.st_size, stat.st_mtime_ns, _sha256(path))

    def pending(self) -> list[tuple[str, Path, tuple[int, int, str]]]:
        """Files whose content differs from what was last backed up."""
        changes = []
        for relative, path in sorted(backed_up_files(self.root).items()):
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            previous = self._synced.get(relative)
            if previous and previous[:2] == (stat.st_size, stat.st_mtime_ns):
                continue
            digest = _sha256(path)
            if previous and previous[2] == digest:
                # Rewritten with identical bytes: nothing to upload.
                self._synced[relative] = (stat.st_size, stat.st_mtime_ns, digest)
                continue
            changes.append((relative, path, (stat.st_size, stat.st_mtime_ns, digest)))
        return changes

    def sync(self, reason: str = "periodic") -> int:
        """Upload changed files; on failure keep them pending for the next attempt."""
        with self._lock:
            try:
                changes = self.pending()
                if not changes:
                    return 0
                self.target.upload([(relative, path) for relative, path, _ in changes], f"SpyMonk backup ({reason})")
            except Exception as exc:
                logger.warning("Backup sync failed; will retry (%s)", safe_exception_context(exc))
                return 0
            for relative, _, state in changes:
                self._synced[relative] = state
        logger.info("Backup synced (files=%s reason=%s)", len(changes), reason)
        return len(changes)

    def start(self) -> None:
        def run() -> None:
            while not self._stop.wait(self.interval):
                self.sync()

        self._thread = threading.Thread(target=run, name="backup-sync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the timer and push what is left."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval + 5)
        self.sync("shutdown")


def configured_target() -> BackupTarget | None:
    if config.BACKUP_TARGET == "hf-dataset":
        return HfDatasetTarget(config.BACKUP_HF_REPO, config.BACKUP_HF_TOKEN, allow_public=config.BACKUP_ALLOW_PUBLIC)
    if config.BACKUP_TARGET == "directory":
        return DirectoryTarget(Path(config.BACKUP_DIRECTORY).expanduser().resolve())
    return None


def create_manager(root: Path) -> BackupManager | None:
    """The configured backup, or None; the caller restores, then starts it."""
    target = configured_target()
    if target is None:
        return None
    return BackupManager(target, root, interval=config.BACKUP_INTERVAL_SECONDS)


def main(argv: list[str] | None = None) -> int:
    """`push` copies this machine's archive to the backup; `pull` copies the
    backup into this machine's data directory without overwriting files."""
    import argparse

    parser = argparse.ArgumentParser(description="Copy the archive to or from the configured backup.")
    parser.add_argument("action", choices=("push", "pull"))
    args = parser.parse_args(argv)
    manager = create_manager(config.DATA_DIR)
    if manager is None:
        raise SystemExit("Set BACKUP_TARGET (and BACKUP_HF_REPO with BACKUP_HF_TOKEN) first.")
    if args.action == "pull":
        print(f"Restored {manager.restore()} files into {config.DATA_DIR}")
        return 0
    manager.target.prepare()
    print(f"Backed up {manager.sync('initial upload')} files to the {manager.target.description}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
