"""SHA-256 integrity: hashing, manifest verification.

Pure stdlib. These functions compute digests and decide whether
they match a manifest; they never move, publish, or delete files.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .manifest import MANIFEST_VERSION, Manifest


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


def verify_files(base_dir: str | Path, manifest: dict | Manifest) -> list[str]:
    """Re-compute SHA-256 + size for every file the manifest lists.

    Returns a list of problems; empty means the transfer is intact.
    """
    base = Path(base_dir)
    m = manifest.to_dict() if isinstance(manifest, Manifest) else manifest
    entries = []
    for entry in m.get("files", []):
        p = base / entry["name"]
        if not p.exists():
            entries.append({"name": entry["name"], "size": None, "sha256": None})
        else:
            entries.append(
                {"name": entry["name"], "size": p.stat().st_size, "sha256": sha256_file(p)}
            )
    return check_manifest(m, entries)
