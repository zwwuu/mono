"""Resolve the app's data directories (templates/, static/) in every mode.

From source or installed: the app's package directory.
Frozen (PyInstaller): the directory holding the exe, where the build
copies templates/ and static/ flat next to it.
"""

import sys
from pathlib import Path

__all__ = ["app_dir"]


def app_dir():
    """Directory holding this app's templates/ and static/ folders."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent
