"""Shared static logger for every app in the monorepo (stdlib logging only).

Usage:
    from common.logging import log, setup_logging

    setup_logging(level="INFO", log_file="app-one.log")
    log.info("hello")

``log`` is a single module-level logger shared by all apps: import
it anywhere — no passing loggers through constructors, no
``getLogger(__name__)`` calls. Call ``setup_logging`` once at
startup to attach handlers and set the level; every module
importing ``log`` writes to the same configured logger.

``setup_logging`` is idempotent: calling it again updates the level
and attaches a newly requested log file, but never stacks duplicate
handlers for the same destination.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

_DEFAULT_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_LOGGER_NAME = "mono"

__all__ = ["log", "get_logger", "setup_logging"]

log = logging.getLogger(_LOGGER_NAME)
"""The shared static logger: ``from common.logging import log`` anywhere."""
log.propagate = False

_configured = False
_file_handlers = set()


def _coerce_level(level):
    if isinstance(level, int):
        return level
    return getattr(logging, str(level).upper(), logging.INFO)


def setup_logging(
    level="INFO",
    log_file=None,
    fmt=_DEFAULT_FORMAT,
    max_bytes=5 * 1024 * 1024,
    backup_count=3,
):
    """Configure the shared static logger once and return it.

    Args:
        level: "DEBUG"/"INFO"/... or a numeric level. Also honoured
            from the LOG_LEVEL environment variable when level is None.
        log_file: if given, also log to this file (rotated at
            max_bytes, keeping backup_count backups). A repeat call
            with a new log_file attaches it; repeating the same file
            adds nothing.
        fmt: log line format.
        max_bytes, backup_count: rotation policy for log_file.
    """
    global _configured
    if level is None:
        level = os.environ.get("LOG_LEVEL", "INFO")
    level = _coerce_level(level)
    log.setLevel(level)
    if not _configured:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(logging.Formatter(fmt))
        log.addHandler(console)
        _configured = True
    if log_file:
        key = os.path.abspath(log_file)
        if key not in _file_handlers:
            file_handler = RotatingFileHandler(
                log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
            )
            file_handler.setFormatter(logging.Formatter(fmt))
            log.addHandler(file_handler)
            _file_handlers.add(key)
    return log


def get_logger(name):
    """Return a named logger (kept for compatibility; prefer ``log``)."""
    return logging.getLogger(name)
