"""Shared libraries for every app in the monorepo, stdlib-only.

Subpackages:

- ``common.logging`` — logger setup (``setup_logging``, ``get_logger``).
- ``common.mail`` — SMTP mail client (``MailClient``, ``MailConfig``).
- ``common.utils`` — paths, config, JSON, and small utilities
  (``load_app_config``, ``app_file_path``, ``retry``, ...).

Import from the subpackage you need, e.g.
``from common.logging import setup_logging``.
"""
