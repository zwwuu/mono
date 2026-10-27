"""Stage 1: find the notes to index."""

from pathlib import Path

__all__ = ["find_notes"]


def find_notes(folder, pattern):
    """Note files under folder matching pattern, sorted by name.

    Returns an empty list when the folder does not exist, so an
    empty folder is a valid (empty) index rather than a crash.
    """
    root = Path(folder)
    if not root.is_dir():
        return []
    return sorted(p for p in root.glob(pattern) if p.is_file())
