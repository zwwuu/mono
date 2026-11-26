"""Run identity: timestamps, run ids, and the idempotency key.

Pure stdlib. Nothing here touches a file or a database.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_run_id() -> str:
    """Short unique id for one run. Also used as the idempotency key base."""
    return uuid.uuid4().hex[:16]


def idempotency_key(job: str, run_id: str) -> str:
    """The key that makes a re-seen run a no-op: same job + run, once.

    The run_id IS the run's identity -- it is random per run, so the
    key is unique per run without any content hash. A combined hash
    of the file set would add nothing: it is opaque (it names no
    file), all-or-nothing (one changed byte re-identifies the whole
    batch), and unverifiable (no single file on disk hashes to it).
    Per-file identity lives where it is actually checked: each
    file's own sha256 in the manifest's ``files[]``."""
    return f"{job}|{run_id}"
