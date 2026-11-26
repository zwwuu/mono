"""Ports: the interfaces the application layer depends on.

A use case only ever talks to a port, never to a concrete class.
That is what makes the layers swappable: the ledger can move from
SQLite to Postgres by writing a new RunRepository, and tests can
substitute a fake repository or a fake transport. Ports are
Protocols, so any class with matching methods satisfies them --
no inheritance needed.
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
    """A file transport: FTP 1 or FTP 2, local / FTP / SFTP.

    Paths are always (remote_dir, remote_name) pairs, with
    remote_dir relative to the transport's root (local) or login
    directory (FTP/SFTP). Every upload goes via ``<name>.part`` +
    rename, so the far side never sees a partial file.
    """

    kind: str

    def upload(self, local_path: str | Path, remote_dir: str, remote_name: str) -> None: ...

    def download(self, remote_dir: str, remote_name: str, local_path: str | Path) -> None: ...

    def list_names(self, remote_dir: str) -> list[str]:
        """Names (not paths) present in remote_dir; [] if it does not exist."""
        ...

    def delete(self, remote_dir: str, remote_name: str) -> None: ...

    def remote_size(self, remote_dir: str, remote_name: str) -> int | None: ...

    def remote_mtime(self, remote_dir: str, remote_name: str) -> float | None: ...

    def move(self, remote_dir: str, remote_name: str, dest_dir: str, dest_name: str) -> None:
        """Move a file within one transport (rename)."""
        ...

    def makedirs(self, remote_dir: str) -> None:
        """Ensure a remote directory exists, creating parents as needed."""
        ...

    def upload_many_with_manifest(
        self,
        files: list[tuple[str | Path, str]],
        manifest_path: str | Path,
        remote_dir: str,
    ) -> None:
        """Upload every data file first, manifest last."""
        ...

    def close(self) -> None: ...

    def clone(self) -> Transport:
        """A fresh transport of the same kind, for a worker thread."""
        ...

    def upload_verified(self, local_path: str | Path, remote_dir: str, remote_name: str) -> None:
        """Upload one file and verify what landed (size, then SHA-256
        download-back)."""
        ...

    def upload_many(self, files: list[tuple[str | Path, str]], remote_dir: str) -> None:
        """Upload several files (concurrently when more than one),
        each verified; a failure raises and no manifest follows."""
        ...
