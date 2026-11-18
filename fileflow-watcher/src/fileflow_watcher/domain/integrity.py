"""Pure domain rules: no I/O, no dependencies outside the stdlib.

These are the checks the whole pipeline's correctness rests on:
manifest verification against observed bytes, safe rename patterns,
and the idempotency key. The infrastructure layer computes the
digests (it owns the files); this module decides whether they match.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .entities import MANIFEST_VERSION, Manifest


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


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(name: str) -> str:
    """Strip path separators / unsafe chars so a name can never escape its dir."""
    name = Path(name).name
    return _SAFE.sub("_", name) or "file"


def render_name(pattern: str, *, name: str, ext: str, job: str, run_id: str) -> str:
    """Render a rename pattern. Tokens: {date} {time} {name} {ext} {job} {run_id}."""
    now = datetime.now()
    rendered = pattern.format(
        date=now.strftime("%Y%m%d"),
        time=now.strftime("%H%M%S"),
        name=name,
        ext=ext,
        job=job,
        run_id=run_id,
    )
    rendered = safe_name(rendered)
    if rendered in (".", "..", "") or "/" in rendered or "\\" in rendered:
        raise ValueError(f"rename pattern produced an unsafe name: {pattern!r}")
    return rendered


def check_manifest(manifest: dict | Manifest, entries: list[dict]) -> list[str]:
    """Verify observed file digests against a manifest.

    ``entries`` is one dict per listed file -- {"name", "size",
    "sha256"} -- with size/sha256 None when the file is missing.
    Returns a list of problems; empty means the transfer is intact.
    """
    m = manifest.to_dict() if isinstance(manifest, Manifest) else manifest
    problems: list[str] = []
    if m.get("version") != MANIFEST_VERSION:
        problems.append(f"unsupported manifest version: {m.get('version')!r}")
    observed = {e["name"]: e for e in entries}
    for entry in m.get("files", []):
        name = entry["name"]
        seen = observed.get(name)
        if seen is None or seen.get("size") is None:
            problems.append(f"missing file: {name}")
            continue
        if seen["size"] != entry["size"]:
            problems.append(f"size mismatch: {name} expected {entry['size']}, got {seen['size']}")
        if seen.get("sha256") != entry["sha256"]:
            problems.append(f"sha256 mismatch: {name}")
    return problems
