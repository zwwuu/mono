"""app-one: build a short report from its bundled template and email it.

Run from source:  python -m app_one [--dry-run] [--config-dir DIR]
As an exe:        app-one [--dry-run]
Mail is dry-run unless an SMTP host is configured (MAIL_HOST etc.),
so the app always runs without secrets.
"""

from app_one.meta import APP_NAME, __version__

__all__ = ["APP_NAME", "__version__"]
