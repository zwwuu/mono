"""Infrastructure -- sources: where the agent fetches files from.

Each source implements the domain's Source port: download into a
staging directory (via .part + rename, never a partial file) and
return the completed local paths. One run fetches one or many files,
but it is always one manifest. A new source type is a new class
here plus one line in build_source -- no use-case change.
"""

from __future__ import annotations

import shutil
import time
import urllib.request
from pathlib import Path

from ..domain.ports import Source
from .filesystem import PART_SUFFIX, log


class WebsiteSource:
    """HTTP(S) download. Plain urllib on purpose: no extra dependency
    for a file fetch, and the agent never needs a browser here."""

    def __init__(self, cfg: dict):
        self.url = cfg["url"]
        self.timeout = cfg.get("timeout_seconds", 60)

    def download(self, dest_dir: Path, run_id: str) -> list[Path]:
        dest_dir.mkdir(parents=True, exist_ok=True)
        name = Path(urllib.request.urlparse(self.url).path).name or "download"
        final = dest_dir / name
        tmp = dest_dir / (name + PART_SUFFIX)
        req = urllib.request.Request(self.url, headers={"User-Agent": "FileFlow-Agent/1.0"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        tmp.replace(final)
        return [final]


class LocalSource:
    """A file already on disk (a drop-folder, or tests)."""

    def __init__(self, cfg: dict):
        self.path = Path(cfg["path"])

    def download(self, dest_dir: Path, run_id: str) -> list[Path]:
        dest_dir.mkdir(parents=True, exist_ok=True)
        final = dest_dir / self.path.name
        shutil.copyfile(self.path, final)
        return [final]


class DirectorySource:
    """Every stable file in a local directory (a drop folder).

    Files are picked by glob ``pattern`` (default "*"), sorted by
    name, skipping .part files, files still being written (mtime
    younger than ``min_age_seconds``, default 2s), and empty files.
    One run takes every matching file as one batch -- the same idea
    as the watcher's outbound batch, in the forward direction.
    """

    def __init__(self, cfg: dict):
        self.dir = Path(cfg["dir"])
        self.pattern = cfg.get("pattern", "*")
        self.min_age = cfg.get("min_age_seconds", 2)

    def download(self, dest_dir: Path, run_id: str) -> list[Path]:
        dest_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        staged: list[Path] = []
        for src in sorted(self.dir.glob(self.pattern)):
            if not src.is_file() or src.suffix == PART_SUFFIX:
                continue
            if now - src.stat().st_mtime < self.min_age:
                continue  # still being written; try next run
            if src.stat().st_size == 0:
                log.warning("source %s: empty file, skipped", src.name)
                continue
            final = dest_dir / src.name
            tmp = dest_dir / (src.name + PART_SUFFIX)
            shutil.copyfile(src, tmp)
            tmp.replace(final)
            staged.append(final)
        return staged


def build_source(cfg: dict) -> Source:
    kind = cfg["type"]
    if kind == "website":
        return WebsiteSource(cfg)
    if kind == "local":
        return LocalSource(cfg)
    if kind == "local-dir":
        return DirectorySource(cfg)
    raise ValueError(f"unknown source type {kind!r} (want website|local|local-dir)")


def download_source(source: dict, dest_dir: Path, run_id: str) -> list[Path]:
    """Download to .part, then rename. Returns the completed local paths."""
    return build_source(source).download(dest_dir, run_id)
