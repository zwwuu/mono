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
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from fileflow_contract import sha256_file

PART_SUFFIX = ".part"

log = logging.getLogger("fileflow_agent")


def setup_logging(level: str = "INFO", log_file: str | Path | None = None) -> None:
    """Console logging, plus a rotating file log when ``log_file``
    is set (midnight rotation, 7 backups). The file sees the same
    records as the console. ``basicConfig`` is a no-op once a
    handler exists (e.g. under pytest), so the agent logger's own
    level is set here too -- otherwise INFO would never reach the
    file handler. Calling this twice with the same file adds only
    one file handler; calling it with a different file switches to
    the new one (the old file handler is closed first)."""
    lvl = getattr(logging, level.upper(), logging.INFO)
    fmt = "%(asctime)s %(levelname)s %(name)s %(message)s"
    logging.basicConfig(level=lvl, format=fmt)
    log.setLevel(lvl)
    if log_file is None:
        return
    path = Path(log_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, TimedRotatingFileHandler):
            if Path(handler.baseFilename).resolve() == path.resolve():
                return
            # The log file changed (a date template rolled over):
            # stop writing to the old file before opening the new one.
            root.removeHandler(handler)
            handler.close()
    file_handler = TimedRotatingFileHandler(path, when="midnight", backupCount=7)
    file_handler.setFormatter(logging.Formatter(fmt))
    file_handler.setLevel(lvl)
    root.addHandler(file_handler)


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
