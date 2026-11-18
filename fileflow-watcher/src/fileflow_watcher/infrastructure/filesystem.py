"""Infrastructure -- filesystem: hashing, manifests, atomic writes.

This is the only layer that touches raw files. The domain decides
*what* counts as verified (domain.integrity.check_manifest); this
module computes the digests and performs the atomic .part + rename
publication the whole design relies on.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import shutil
from pathlib import Path

from ..domain.entities import Manifest
from ..domain.integrity import check_manifest

PART_SUFFIX = ".part"

log = logging.getLogger("fileflow_watcher")


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


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


def write_manifest(path: str | Path, manifest: dict | Manifest) -> None:
    """Write a manifest atomically: .part first, then rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = manifest.to_dict() if isinstance(manifest, Manifest) else manifest
    tmp = path.with_name(path.name + PART_SUFFIX)
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_manifest(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


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
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + PART_SUFFIX)
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)
    if sha256_file(dst) != sha256_file(src) or dst.stat().st_size != src.stat().st_size:
        raise OSError(f"read-back verification failed after publishing {dst}")
    return dst


def move_tree(src: Path, dst: Path) -> None:
    """Move every entry of src into dst, then remove src."""
    dst.mkdir(parents=True, exist_ok=True)
    for p in src.iterdir():
        shutil.move(str(p), str(dst / p.name))
    src.rmdir()


def read_csv_rows(path: str | Path) -> list[list[str]]:
    """Parse a CSV file. Raises ValueError when it has no rows at all."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    if not rows:
        raise ValueError(f"empty CSV: no header row in {Path(path).name}")
    return rows
