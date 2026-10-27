"""Duplicate-file finder for app-four.

Two-stage approach, so large folders stay fast: group by file size
first (cheap), then SHA-256 only the files that share a size with
another file. A file is excluded when any part of its path relative
to the scanned folder matches an exclude pattern (fnmatch).
"""

import fnmatch
import hashlib
from pathlib import Path

from common.utils import now_iso

CHUNK_SIZE = 1024 * 1024


def _excluded(path, root, patterns):
    parts = path.relative_to(root).parts
    return any(fnmatch.fnmatch(part, pattern) for part in parts for pattern in patterns)


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_duplicates(folder, pattern="*", min_size=0, exclude=()):
    """Return duplicate groups, largest waste first.

    Each group: {"sha256", "size_bytes", "files", "wasted_bytes"}
    where wasted_bytes is what deleting all but one copy would free.
    """
    root = Path(folder)
    files = [
        path
        for path in root.rglob(pattern)
        if path.is_file() and path.stat().st_size >= min_size and not _excluded(path, root, exclude)
    ]
    by_size = {}
    for path in files:
        by_size.setdefault(path.stat().st_size, []).append(path)
    groups = []
    for size, candidates in by_size.items():
        if len(candidates) < 2:
            continue
        by_hash = {}
        for path in candidates:
            by_hash.setdefault(_sha256(path), []).append(path)
        for digest, dupes in by_hash.items():
            if len(dupes) > 1:
                groups.append(
                    {
                        "sha256": digest,
                        "size_bytes": size,
                        "files": sorted(str(p) for p in dupes),
                        "wasted_bytes": size * (len(dupes) - 1),
                    }
                )
    groups.sort(key=lambda group: -group["wasted_bytes"])
    return groups


def build_summary(folder, groups):
    """Aggregate duplicate groups into a report dict."""
    return {
        "generated_at": now_iso(),
        "folder": str(folder),
        "groups": groups,
        "total_groups": len(groups),
        "total_files": sum(len(group["files"]) for group in groups),
        "total_wasted_bytes": sum(group["wasted_bytes"] for group in groups),
    }


def format_summary(summary):
    """Plain-text version of the report, for the email body."""
    header = (
        f"Duplicate scan of {summary['folder']}: {summary['total_groups']} group(s), "
        f"{summary['total_files']} file(s), {summary['total_wasted_bytes']} wasted bytes."
    )
    lines = [header, ""]
    for group in summary["groups"]:
        lines.append(f"{group['wasted_bytes']} wasted bytes, {len(group['files'])} copies:")
        lines.extend(f"  {path}" for path in group["files"])
    return "\n".join(lines)
