"""Refuse to start a second API process on the same data directory.

Topic audits, snapshots, uploads, research jobs, and rate limits are kept per
process. Two processes sharing one SPYMONK_DATA_DIR would race on those files
and on the vector index, so the API takes an exclusive OS lock at startup.
The OS releases the lock automatically if the process dies.
"""

from __future__ import annotations

import os
from pathlib import Path


class InstanceLockError(RuntimeError):
    """Another API process already holds the data directory."""


class InstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise InstanceLockError(
                f"Another SpyMonk API process is already using {self.path.parent}. "
                "Run exactly one API process per data directory (no --workers), or set "
                "SPYMONK_INSTANCE_LOCK=false only if you have replaced the file-backed stores."
            ) from exc
        self._handle = handle

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            handle.close()
