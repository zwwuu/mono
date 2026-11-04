"""Static logger: ``from sftp_uploader.log import log`` anywhere.

``log`` is a single module-level logger. Call ``setup_logging(config)`` once
at startup to attach handlers and set the level; every module importing
``log`` afterwards writes to the same configured logger.
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

log: logging.Logger = logging.getLogger("sftp_uploader")
log.propagate = False


def setup_logging(cfg) -> logging.Logger:
    """Configure the static logger from the ``logging`` config section."""
    lcfg = cfg.get("logging", {}) or {}
    level = getattr(logging, str(lcfg.get("level", "INFO")).upper(), logging.INFO)

    log.setLevel(level)
    log.handlers.clear()  # idempotent: safe to call twice (tests, reloads)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    log.addHandler(console)

    path = lcfg.get("file")
    if path:
        fh = RotatingFileHandler(
            path,
            maxBytes=int(lcfg.get("max_bytes", 1_048_576)),
            backupCount=int(lcfg.get("backup_count", 3)),
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        log.addHandler(fh)

    return log
