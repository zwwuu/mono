"""Tests for the shared static logger in common.logging."""

import logging

import pytest

from common import logging as common_logging
from common.logging import get_logger, log, setup_logging


@pytest.fixture
def clean_log():
    """Reset common.logging to pristine state; restore everything after.

    NB: other test modules (e.g. sftp-uploader's) configure the shared
    logger and pytest attaches its own handlers to non-propagating
    loggers, so save everything, start clean, and put it all back.
    """
    saved_handlers = list(log.handlers)
    saved_level = log.level
    saved_configured = common_logging._configured
    saved_files = set(common_logging._file_handlers)
    for handler in list(log.handlers):
        log.removeHandler(handler)
    common_logging._configured = False
    common_logging._file_handlers.clear()
    log.setLevel(logging.WARNING)
    yield
    for handler in list(log.handlers):
        log.removeHandler(handler)
        handler.close()
    for handler in saved_handlers:
        log.addHandler(handler)
    log.setLevel(saved_level)
    common_logging._configured = saved_configured
    common_logging._file_handlers.clear()
    common_logging._file_handlers.update(saved_files)


def _console_handlers():
    # Exactly logging.StreamHandler: pytest's own handlers are subclasses
    # (NullHandler, FileHandler, LogCaptureHandler) and must not count.
    return [h for h in log.handlers if type(h) is logging.StreamHandler]


def test_log_is_a_shared_static_logger():
    assert isinstance(log, logging.Logger)
    assert log.name == "mono"
    from common.logging import log as log_again

    assert log_again is log


def test_setup_logging_returns_the_shared_log(clean_log):
    assert setup_logging(level="INFO") is log


def test_setup_logging_is_idempotent(clean_log):
    setup_logging(level="INFO")
    setup_logging(level="INFO")
    assert len(_console_handlers()) == 1


def test_setup_logging_updates_level(clean_log):
    setup_logging(level="INFO")
    assert log.level == logging.INFO
    setup_logging(level="DEBUG")
    assert log.level == logging.DEBUG


def test_setup_logging_attaches_file_once(tmp_path, clean_log):
    log_file = tmp_path / "app.log"
    setup_logging(level="INFO", log_file=str(log_file))
    setup_logging(level="INFO", log_file=str(log_file))
    assert log_file.is_file()
    file_handlers = [
        h
        for h in log.handlers
        if isinstance(h, logging.FileHandler)
        and getattr(h, "baseFilename", None) == str(log_file)
    ]
    assert len(file_handlers) == 1
    log.info("hello file")
    assert "hello file" in log_file.read_text(encoding="utf-8")


def test_get_logger_still_available():
    assert get_logger("something") is logging.getLogger("something")
