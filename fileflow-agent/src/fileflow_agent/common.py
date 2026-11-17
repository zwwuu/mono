"""Shared integrity helpers for the FileFlow agent and watcher.

No approval step anywhere in this design. Correctness comes from:
  * SHA-256 + size recorded in a manifest at every hop, re-verified
    before the next step runs;
  * atomic publication: write ``<name>.part``, then rename to ``<name>``.
    A watcher only ever acts on a fully-renamed file, never on a .part;
  * the manifest is uploaded / published *last*, and its rename is the
    trigger that tells the watcher a transfer is complete.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

MANIFEST_VERSION = 1
PART_SUFFIX = ".part"

log = logging.getLogger("fileflow_agent")


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_run_id() -> str:
    """Short unique id for one run. Also used as the idempotency key base."""
    return uuid.uuid4().hex[:16]


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def file_entry(path: str | Path, role: str) -> dict:
    p = Path(path)
    return {
        "name": p.name,
        "role": role,
        "size": p.stat().st_size,
        "sha256": sha256_file(p),
    }


def write_manifest(path: str | Path, manifest: dict) -> None:
    """Write a manifest atomically: .part first, then rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + PART_SUFFIX)
    tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_manifest(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def verify_files(base_dir: str | Path, manifest: dict) -> list[str]:
    """Re-compute SHA-256 + size for every file the manifest lists.

    Returns a list of problems; empty means the transfer is intact.
    """
    base = Path(base_dir)
    problems: list[str] = []
    if manifest.get("version") != MANIFEST_VERSION:
        problems.append(f"unsupported manifest version: {manifest.get('version')!r}")
    for entry in manifest.get("files", []):
        p = base / entry["name"]
        if not p.exists():
            problems.append(f"missing file: {entry['name']}")
            continue
        if p.stat().st_size != entry["size"]:
            problems.append(
                f"size mismatch: {entry['name']} "
                f"expected {entry['size']}, got {p.stat().st_size}"
            )
        actual = sha256_file(p)
        if actual != entry["sha256"]:
            problems.append(f"sha256 mismatch: {entry['name']}")
    return problems


def atomic_move(src: str | Path, dst: str | Path) -> Path:
    """Move a file within one filesystem (rename). Never a copy."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)
    return dst


def atomic_publish(src: str | Path, dst: str | Path) -> Path:
    """Copy src to dst via .part + rename, then read it back and verify.

    The destination never sees a partial file, and the caller knows the
    published bytes are exactly the source bytes.
    """
    import shutil

    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + PART_SUFFIX)
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)
    if sha256_file(dst) != sha256_file(src) or dst.stat().st_size != src.stat().st_size:
        raise IOError(f"read-back verification failed after publishing {dst}")
    return dst


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
