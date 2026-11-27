"""Ports: the interfaces the application layer depends on.

A use case only ever talks to a port, never to a concrete class.
That is what makes the layers swappable: the ledger can move from
SQLite to Postgres by writing a new RunRepository, a transport can
be replaced without touching a use case, and tests can substitute
a fake repository or a local transport. Ports are Protocols, so any
class with matching methods satisfies them -- no inheritance needed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .entities import RunRecord


class RunRepository(Protocol):
    """The ledger, as the application sees it."""

    def claim(self, *, run_id: str, job: str, idempotency_key: str) -> bool:
        """Record a new run. False if this idempotency key was seen."""
        ...

    def set_state(self, run_id: str, state: str, **detail) -> None: ...

    def get(self, run_id: str) -> RunRecord | None: ...

    def seen_key(self, idempotency_key: str) -> dict | None: ...

    def list_by_state(self, state: str) -> list[RunRecord]: ...

    def close(self) -> None: ...


class Transport(Protocol):
    """Upload/download to the FTP server, whatever the mechanism."""

    def upload(self, local_path: str | Path, remote_dir: str, remote_name: str) -> None: ...

    def download(self, remote_dir: str, remote_name: str, local_path: str | Path) -> None: ...

    def list_names(self, remote_dir: str) -> list[str]: ...

    def delete(self, remote_dir: str, remote_name: str) -> None: ...

    def remote_size(self, remote_dir: str, remote_name: str) -> int | None: ...

    def remote_mtime(self, remote_dir: str, remote_name: str) -> float | None:
        """mtime of a remote file as epoch seconds, or None when the
        transport cannot tell (unsupported, or the file is gone).
        Sources use it to skip files still being written."""
        ...

    def upload_many_with_manifest(
        self,
        files: list[tuple[str | Path, str]],
        manifest_path: str | Path,
        remote_dir: str,
    ) -> None:
        """Upload every data file first, manifest last (its rename is
        the trigger that tells the watcher the batch is complete)."""
        ...

    def upload_verified(self, local_path: str | Path, remote_dir: str, remote_name: str) -> None:
        """Upload one file and verify what landed (size + SHA-256)."""
        ...

    def upload_many(self, files: list[tuple[str | Path, str]], remote_dir: str) -> None:
        """Upload several files, each verified; concurrent above one."""
        ...

    def clone(self) -> Transport:
        """A fresh transport of the same kind, for a worker thread."""
        ...

    def close(self) -> None: ...


class Source(Protocol):
    """Where the agent fetches files from (website, local file, local dir)."""

    def download(self, dest_dir: Path, run_id: str) -> list[Path]:
        """Fetch the source file(s) into dest_dir; return their local paths.
        One file for a website/local source, every matching file for a
        local-dir source -- one run, one manifest, however many files."""
        ...


class Procedure(Protocol):
    """A business procedure: read input, write a NEW output file."""

    def __call__(self, input_path: Path, workdir: Path, ctx: dict) -> Path: ...


class Notifier(Protocol):
    """Email (or log-only) notification on run completion. One
    call per run: STAGED / UPLOADED on success, FAILED with the
    error on failure. SKIPPED is never notified."""

    def notify(
        self,
        *,
        job: str,
        run_id: str,
        status: str,
        files: list[str],
        destination: str | None = None,
        error: str | None = None,
        summary: str | None = None,
    ) -> None: ...
