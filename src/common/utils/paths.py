"""Path helpers that work both from source and inside a PyInstaller exe.

The key fact: when frozen, the exe runs from dist/<app>/, and data
sits flat next to it. These helpers hide the source-vs-frozen
difference; app code should never build config/asset paths by hand.
"""

import importlib.util
import os
import sys
from pathlib import Path

__all__ = ["exe_dir", "installed_package_dir", "is_frozen", "repo_app_dir"]


def is_frozen():
    """True when running as a PyInstaller executable."""
    return getattr(sys, "frozen", False)


def exe_dir():
    """Dir holding the exe when frozen, else the current working dir."""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.getcwd()


def repo_app_dir(app_name):
    """The app's package dir inside a source checkout: src/apps/<package>/.

    Only meaningful from source: this file lives at
    src/common/utils/paths.py, so parents[3] is the repo root, and the
    package is the app name with "-" replaced by "_". Returns the path
    whether or not it exists; callers check existence.
    """
    package = app_name.replace("-", "_")
    return str(Path(__file__).resolve().parents[3] / "src" / "apps" / package)


def installed_package_dir(app_name):
    """The app's package dir in an installed (wheel) layout, or None.

    Resolves through sys.path, so in a source checkout it points at
    src/apps/<package>/ (the same dir repo_app_dir() returns), and in
    a wheel install at site-packages/apps/<package>/. Returns None
    when the package is not importable.
    """
    package = app_name.replace("-", "_")
    for module_name in (f"apps.{package}", package):
        spec = importlib.util.find_spec(module_name)
        if spec is not None and spec.submodule_search_locations:
            return str(Path(spec.submodule_search_locations[0]))
    return None
