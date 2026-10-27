"""JSON load/save helpers."""

import json
import os

__all__ = ["load_json", "save_json"]


def load_json(path, default=None):
    """Load a JSON file; return `default` when it does not exist."""
    if not os.path.isfile(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data, indent=2):
    """Write JSON, creating parent directories as needed."""
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent)
        f.write("\n")
