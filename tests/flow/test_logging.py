#!/usr/bin/env python3
"""FileFlow logging tests — runnable with pytest or standalone.

Covers: date-rotated file sink + console sink setup, level filtering,
no duplicate sinks on re-setup, and engine log mirroring to the file.

    pytest tests/test_logging.py
    python tests/test_logging.py
"""

import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from fileflow.infra.demo import ensure_demo_source
from fileflow.infra.logging_setup import setup_logging
from helpers import make_engine, wait_status

from common.logging import log


def _read_today_log(log_dir):
    path = os.path.join(log_dir, f"fileflow-{date.today().isoformat()}.log")
    assert os.path.exists(path), f"expected log file {path}"
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_setup_logging_writes_dated_file_and_filters_level():
    with tempfile.TemporaryDirectory() as tmp:
        setup_logging(tmp, level="ERROR", retention_days=7)
        log.info("this info must not land in the file")
        log.error("this error must land in the file")
        text = _read_today_log(os.path.join(tmp, "logs"))
        assert "this error must land in the file" in text
        assert "this info must not land in the file" not in text


def test_setup_logging_is_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        setup_logging(tmp, level="INFO")
        setup_logging(tmp, level="INFO")  # same params: no duplicate sinks
        log.info("exactly once")
        text = _read_today_log(os.path.join(tmp, "logs"))
        assert text.count("exactly once") == 1, text


def test_engine_log_mirrors_to_file():
    task = {
        "id": "log-mirror",
        "name": "log-mirror",
        "enabled": True,
        "approval": False,
        "rename": "{date}_{name}{ext}",
        "destinations": [{"type": "local", "dir": "log-mirror-out"}],
        "notify": False,
    }
    with tempfile.TemporaryDirectory() as tmp:
        setup_logging(tmp, level="INFO")
        cfg, db, engine, _reg = make_engine(tmp, [task])
        ensure_demo_source(cfg)
        run_id = engine.create_and_start("log-mirror")
        wait_status(db, run_id, "COMPLETED")
        text = _read_today_log(os.path.join(tmp, "logs"))
        assert "Run COMPLETED" in text, text
        assert f"[run #{run_id}]" in text, text


if __name__ == "__main__":
    test_setup_logging_writes_dated_file_and_filters_level()
    test_setup_logging_is_idempotent()
    test_engine_log_mirrors_to_file()
    print("test_logging: all passed")
