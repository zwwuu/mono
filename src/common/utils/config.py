"""Per-app config and bundled-asset loading.

Config precedence (first file found wins, merged over `defaults`):
    1. any directory in `search_dirs` (tests, --config-dir),
    2. when frozen: <folder>/ next to the exe (the deployed copy,
       editable in place),
    3. from source: src/<package>/<folder>/<filename>, or the
       installed package dir when there is no source checkout.

Merging is recursive for nested dicts (lists and scalars replace).
A malformed config file raises ValueError naming the file; a
non-dict config file raises TypeError.

"config" is just the default folder: folder names are arbitrary per
app, so pass folder="settings" (or any name) to look somewhere else.
The same goes for assets: pass "assets" as the first part to
app_file_path().

Any other bundled file — a template .docx/.xlsx, a template folder
with an arbitrary name — resolves through app_file_path(), which
takes any path inside the app's bundle.
"""

import json
from pathlib import Path

from .jsonio import load_json
from .paths import exe_dir, installed_package_dir, is_frozen, repo_app_dir

__all__ = ["app_file_path", "load_app_config", "read_app_asset"]


def _first_existing(candidates):
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def _deep_merge(base, override):
    """Merge override into a copy of base: dicts merge recursively,
    lists and scalars replace."""
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_app_config(
    app_name, filename="config.json", defaults=None, search_dirs=None, folder="config"
):
    """Load an app's config as a dict: defaults + first file found.

    `folder` is the config folder's name inside the app's bundle
    ("config" by default — folder names are arbitrary per app, so an
    app that keeps its config elsewhere passes its own name here).
    Nested dicts merge recursively; lists and scalars replace.
    """
    data = dict(defaults or {})
    candidates = [Path(d) / filename for d in (search_dirs or [])]
    if is_frozen():
        candidates.append(Path(exe_dir()) / folder / filename)
    else:
        candidates.append(Path(repo_app_dir(app_name)) / folder / filename)
        installed = installed_package_dir(app_name)
        if installed:
            candidates.append(Path(installed) / folder / filename)
    found = _first_existing(candidates)
    if found is None:
        return data
    try:
        content = load_json(found)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON in config file {found}: {e}") from e
    if not isinstance(content, dict):
        raise TypeError(f"config file {found} must contain a JSON object")
    return _deep_merge(data, content)


def app_file_path(app_name, *parts):
    """Absolute path of any file inside the app's bundle, or None when missing.

    Takes any path — for the default assets folder, pass "assets" as
    the first part. Use it for template files and folders with
    arbitrary names, e.g.
    app_file_path("app-two", "templates", "monthly-summary.xlsx").
    Frozen: <parts> next to the exe; from source: src/<package>/<parts>.
    """
    candidates = []
    if is_frozen():
        candidates.append(Path(exe_dir()) / Path(*parts))
    else:
        candidates.append(Path(repo_app_dir(app_name)) / Path(*parts))
        installed = installed_package_dir(app_name)
        if installed:
            candidates.append(Path(installed) / Path(*parts))
    found = _first_existing(candidates)
    return str(found) if found else None


def read_app_asset(app_name, *parts, encoding="utf-8"):
    """Text of a bundled asset, or None when it is missing."""
    path = app_file_path(app_name, "assets", *parts)
    if path is None:
        return None
    with open(path, encoding=encoding) as f:
        return f.read()
