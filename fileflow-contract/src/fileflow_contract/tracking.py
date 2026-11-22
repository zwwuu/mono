"""Processed-file tracking in the ledger (layer-2 idempotency).

Layer 1 is the run key ``job|run_id`` (same run re-delivered =
duplicate). Layer 2 is this: a completed run records the files it
processed -- job + name + hash each -- in its ledger ``detail``
(``files=[{"name", "sha256"}, ...]``), and the next run skips any
file already in a completed run's list. A changed file has a new
hash, so it is processed; a file from a failed run was never
recorded, so it retries.

This helper is the lookup both sides share: the agent's download
(skip already-uploaded files) and the watcher's outbound staging
(archive already-staged re-drops instead of staging them again).
It takes already-listed records -- the caller decides which states
count as completed -- and only reads ``.job`` and ``.detail`` off
each record, so it works with any ledger record shape that carries
those two fields.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def file_already_recorded(records: Iterable[Any], job: str, name: str, sha256: str) -> bool:
    """True if this exact file (job + name + hash) is in any of the
    given completed runs' recorded file lists."""
    for record in records:
        if record.job != job:
            continue
        for f in record.detail.get("files", []):
            if f.get("name") == name and f.get("sha256") == sha256:
                return True
    return False
