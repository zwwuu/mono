"""Shared fixtures for FileFlow tests (pytest and standalone runs).

Every engine test needs the same scaffolding: a Config pointed at a
temporary data dir, a tasks.json, a DB, a registry and an Engine.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from fileflow.config import Config  # noqa: E402
from fileflow.core.engine import Engine  # noqa: E402
from fileflow.core.tasks import TaskRegistry  # noqa: E402
from fileflow.infra.db import DB  # noqa: E402

DATA_SUBDIRS = (
    "work",
    "archive",
    "outbox",
    "demo_ftp/inbox",
    "demo_ftp/processed",
    "demo_smb",
    "demo_source",
)


def cfg_for(tmp):
    return Config(
        raw={"app": {"data_dir": os.path.join(tmp, "data")}},
        source_path=os.path.join(tmp, "config.json"),
    )


def registry_for(tmp, tasks):
    cfg = cfg_for(tmp)
    path = os.path.join(tmp, "tasks.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"tasks": tasks}, f)
    return cfg, TaskRegistry(cfg)


def open_registry(tmp):
    """A registry over an existing tasks.json, without rewriting it."""
    return cfg_for(tmp), TaskRegistry(cfg_for(tmp))


def make_engine(tmp, tasks):
    """(cfg, db, engine, registry) for a demo-mode engine over tmp."""
    cfg = cfg_for(tmp)
    cfg.raw.update(
        {
            "demo": {
                "enabled": True,
                "source_file": "input_report.csv",
                "process_delay_seconds": 2,
            },
            "ftp": {"poll_seconds": 1},
            "smtp": {"host": "", "from": "fileflow@localhost"},
            "notify": {"recipients": ["ops@example.com"], "on_stuck": True},
            "steps": {"timeout_seconds": 60},
            "scheduler": {"tick_seconds": 0.5},
        }
    )
    path = os.path.join(tmp, "tasks.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"tasks": tasks}, f)
    os.makedirs(cfg.data_dir, exist_ok=True)
    for sub in DATA_SUBDIRS:
        os.makedirs(cfg.path(sub), exist_ok=True)
    db = DB(cfg.path("fileflow.db"))
    registry = TaskRegistry(cfg)
    return cfg, db, Engine(cfg, db, registry), registry


def wait_status(db, run_id, want, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = db.get_run(run_id)
        if run["status"] == want:
            return run
        time.sleep(0.2)
    raise AssertionError(f"run {run_id} never reached {want}; now {db.get_run(run_id)['status']}")


def wait_approval(db, run_id, key, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = db.get_run(run_id)
        if run["status"] == "AWAITING_APPROVAL" and run["current_step"] == key:
            return
        time.sleep(0.2)
    raise AssertionError(
        f"run {run_id} never waited at approval {key}; "
        f"now {db.get_run(run_id)['status']} / {db.get_run(run_id)['current_step']}"
    )


def outbox_texts(cfg):
    out = []
    for name in os.listdir(cfg.path("outbox")):
        with open(cfg.path("outbox", name), encoding="utf-8") as f:
            out.append(f.read())
    return out


def seed(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
