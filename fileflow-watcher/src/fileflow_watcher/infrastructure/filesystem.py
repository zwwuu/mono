"""Infrastructure -- filesystem: atomic writes, CSV parsing.

This is the only layer that touches raw files. Hashing and manifest
verification live in the shared ``fileflow_contract`` package; this
module performs the atomic .part + rename publication the whole
design relies on.
"""

from __future__ import annotations

import csv
import logging
import os
import shutil
from pathlib import Path

from fileflow_contract import sha256_file

PART_SUFFIX = ".part"

log = logging.getLogger("fileflow_watcher")


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


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
