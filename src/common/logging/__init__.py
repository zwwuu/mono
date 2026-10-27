"""Shared logging for every app in the monorepo (stdlib logging only).

Usage:
    from common.logging import setup_logging, get_logger

    setup_logging(level="INFO", log_file="app-one.log")
    log = get_logger(__name__)
    log.info("hello")

`setup_logging` is idempotent: calling it again updates the level
and attaches a newly requested log file, but never stacks duplicate
handlers for the same file.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

_DEFAULT_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_configured = set()
_file_handlers = {}

__all__ = ["get_logger", "setup_logging"]


def _coerce_level(level):
    if isinstance(level, int):
        return level
    return getattr(logging, str(level).upper(), logging.INFO)


def setup_logging(level="INFO", log_file=None, name=None, fmt=_DEFAULT_FORMAT):
    """Configure logging once and return the logger.

    Args:
        level: "DEBUG"/"INFO"/... or a numeric level. Also honoured
            from the LOG_LEVEL environment variable when level is None.
        log_file: if given, also log to this file (5 MB rotation,
            3 backups). A repeat call with a new log_file attaches
            it; repeating the same file adds nothing.
        name: logger name to configure (None = root logger).
    """
    if level is None:
        level = os.environ.get("LOG_LEVEL", "INFO")
    level = _coerce_level(level)
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if name not in _configured:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(logging.Formatter(fmt))
        logger.addHandler(console)
        logger.propagate = False
        _configured.add(name)
        _file_handlers[name] = set()
    if log_file:
        key = os.path.abspath(log_file)
        if key not in _file_handlers[name]:
            file_handler = RotatingFileHandler(
                log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
            )
            file_handler.setFormatter(logging.Formatter(fmt))
            logger.addHandler(file_handler)
            _file_handlers[name].add(key)
    return logger


def get_logger(name):
    """Return a named logger (call setup_logging first to configure it)."""
    return logging.getLogger(name)
