"""app-three: check a list of URLs and report health.

Run from source:  python -m app_three [--config-dir DIR]
As an exe:        app-three
Writes a JSON report, logs the result, and emails a summary when any
URL is down (or always, per config) — dry-run unless SMTP is
configured, so it always runs without secrets.
"""

from app_three.meta import APP_NAME, __version__

__all__ = ["APP_NAME", "__version__"]
