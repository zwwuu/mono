"""Shared libraries for every app in the monorepo, stdlib-only —
except ``common.db``, which needs SQLAlchemy.

Subpackages:

- ``common.app_config`` — bootstrap template for linear apps
  (``AppConfig``, ``Router``): args, config, logging, paths.
- ``common.cli`` — standard argparse flags (``build_parser``,
  ``resolve_log_level``).
- ``common.config`` — validated JSON config (``Config``,
  ``ConfigError``, ``load_json``, ``check_required``,
  ``resolve_secret``, ``redact``).
- ``common.db`` — SQLAlchemy setup shared by apps (``Base``,
  ``Database``, ``create_engine_for``). Needs SQLAlchemy.
- ``common.logging`` — the shared static logger (``log``) plus ``setup_logging``.
- ``common.mail`` — SMTP mail client (``MailClient``, ``MailConfig``).
- ``common.sysinfo`` — runtime system, user, and network info
  (``system_info``, ``user_info``, ``network_info``, ``runtime_info``,
  ``all_info``). Windows first.
- ``common.utils`` — paths, config, JSON, and small utilities
  (``load_app_config``, ``app_file_path``, ``retry``, ...).

Import from the subpackage you need, e.g.
``from common.logging import log, setup_logging``.
"""
