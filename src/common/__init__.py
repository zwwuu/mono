"""Shared libraries for every app in the monorepo, stdlib-only.

Subpackages:

- ``common.cli`` — standard argparse flags (``build_parser``,
  ``resolve_log_level``).
- ``common.config`` — validated JSON config (``Config``,
  ``ConfigError``, ``load_json``, ``check_required``,
  ``resolve_secret``, ``redact``).
- ``common.logging`` — the shared static logger (``log``) plus ``setup_logging``.
- ``common.mail`` — SMTP mail client (``MailClient``, ``MailConfig``).
- ``common.utils`` — paths, config, JSON, and small utilities
  (``load_app_config``, ``app_file_path``, ``retry``, ...).

Import from the subpackage you need, e.g.
``from common.logging import log, setup_logging``.
"""
