# FileFlow logging: console + one log file per date, on the shared static log.
#
# Sinks (configured once at startup by App, see app.py):
#   - stdout (console), via common.logging's setup,
#   - <data_dir>/logs/fileflow-YYYY-MM-DD.log, rotated at midnight
#     (a new file per date) and kept for `retention_days` (default 30).
#
# Everything the app does flows through the shared static log from
# common.logging: engine run/step logs (which also live in the DB,
# for the dashboard), scheduler fires, and server/startup lines.
# Per-step detail stays in the dashboard log stream; this file is the
# operator's tail-able transcript of the whole app.
import logging
import os
import re
from datetime import date, timedelta
from logging.handlers import TimedRotatingFileHandler

from common.logging import log
from common.logging import setup_logging as _common_setup_logging

_FORMAT = "%(asctime)s | %(levelname)-8s | %(message)s"

_current = None


def _prune_old_logs(log_dir, retention_days):
    """Delete fileflow-YYYY-MM-DD.log files older than retention_days."""
    cutoff = date.today() - timedelta(days=retention_days)
    for entry in os.listdir(log_dir):
        match = re.fullmatch(r"fileflow-(\d{4}-\d{2}-\d{2})\.log", entry)
        if not match:
            continue
        try:
            if date.fromisoformat(match.group(1)) < cutoff:
                os.remove(os.path.join(log_dir, entry))
        except (OSError, ValueError):
            pass


def setup_logging(data_dir, level="INFO", retention_days=30):
    """Console + date-rotated file sinks on the shared static log.

    Reconfigures only when the target changes, so repeated App
    construction (tests, reloads) does not stack duplicate sinks.
    Returns the logger.
    """
    global _current
    params = (os.path.abspath(data_dir), str(level).upper(), int(retention_days))
    if _current == params:
        return log
    # Drop this app's previously attached file sinks.
    for handler in [h for h in log.handlers if getattr(h, "_fileflow_sink", False)]:
        log.removeHandler(handler)
        handler.close()
    _common_setup_logging(level=params[1])  # console sink (idempotent)
    try:
        log_dir = os.path.join(params[0], "logs")
        os.makedirs(log_dir, exist_ok=True)
        file_handler = TimedRotatingFileHandler(
            os.path.join(log_dir, f"fileflow-{date.today().isoformat()}.log"),
            when="midnight",
            interval=1,
            backupCount=params[2],
            encoding="utf-8",
        )
        file_handler._fileflow_sink = True
        file_handler.setFormatter(logging.Formatter(_FORMAT))
        file_handler.setLevel(params[1])
        log.addHandler(file_handler)
        _prune_old_logs(log_dir, params[2])
    except OSError as e:
        log.warning("could not set up log file, console only: %s", e)
    _current = params
    return log
