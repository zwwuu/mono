"""Ports: the interfaces the application layer depends on.

A use case only ever talks to a port, never to a concrete class.
That is what makes the layers swappable: the ledger can move from
SQLite to Postgres by writing a new RunRepository, and tests can
substitute a fake repository. Ports are Protocols, so any class
with matching methods satisfies them -- no inheritance needed.
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


class Procedure(Protocol):
    """A business procedure: read input, write a NEW output file."""

    def __call__(self, input_path: Path, workdir: Path, ctx: dict) -> Path: ...
