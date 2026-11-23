"""File logging tests: setup_logging writes to log_file when set,
and adds no file handler when it is not.
"""

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fileflow_watcher.infrastructure.filesystem import log, setup_logging


def _file_handlers() -> list:
    return [h for h in logging.getLogger().handlers if isinstance(h, TimedRotatingFileHandler)]


def _close_file_handler(path: Path) -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        if isinstance(h, TimedRotatingFileHandler) and Path(h.baseFilename) == path:
            h.close()
            root.removeHandler(h)


def test_setup_logging_with_log_file_writes(tmp_path: Path):
    log_file = tmp_path / "watcher.log"
    setup_logging("INFO", log_file=log_file)
    try:
        log.info("hello log file")
        for h in _file_handlers():
            h.flush()
        assert "hello log file" in log_file.read_text(encoding="utf-8")
    finally:
        _close_file_handler(log_file)


def test_setup_logging_without_log_file_adds_no_file_handler(tmp_path: Path):
    before = _file_handlers()
    setup_logging("INFO")
    after = _file_handlers()
    assert after == before
    assert not (tmp_path / "watcher.log").exists()
