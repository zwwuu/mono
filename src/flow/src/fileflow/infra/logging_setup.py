# FileFlow — loguru logging: console + one log file per date.
#
# Sinks (configured once at startup by App, see app.py):
#   - stderr (console), so `fileflow` still talks while it runs;
#   - <data_dir>/logs/fileflow-YYYY-MM-DD.log, rotated at midnight
#     (a new file per date) and kept for `retention_days` (default 30).
#
# Everything the app does flows through the shared loguru logger:
# engine run/step logs (which also live in the DB, for the dashboard),
# scheduler fires, and server/startup lines. Per-step detail stays in
# the dashboard log stream; this file is the operator's tail-able
# transcript of the whole app.
import os
import sys

from loguru import logger

_FORMAT = "{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {name}:{function}:{line} | {message}"

_current = None


def setup_logging(data_dir, level="INFO", retention_days=30):
    """Configure console + date-rotated file sinks. Reconfigures only
    when the target changes, so repeated App construction (tests,
    reloads) does not stack duplicate sinks. Returns the logger."""
    global _current
    params = (os.path.abspath(data_dir), str(level).upper(), int(retention_days))
    if _current == params:
        return logger
    logger.remove()
    logger.add(sys.stderr, level=params[1], format=_FORMAT)
    try:
        log_dir = os.path.join(params[0], "logs")
        os.makedirs(log_dir, exist_ok=True)
        logger.add(
            os.path.join(log_dir, "fileflow-{time:YYYY-MM-DD}.log"),
            rotation="00:00",
            retention=f"{params[2]} days",
            level=params[1],
            format=_FORMAT,
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning(f"could not set up log file, console only: {e}")
    _current = params
    return logger
