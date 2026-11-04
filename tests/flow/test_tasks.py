#!/usr/bin/env python3
"""FileFlow task-engine unit tests — runnable with pytest or standalone.

Covers: rename templates, task validation, pipeline building from
shorthand, registry CRUD + reload, scheduler task resolution, and the
multi-approval / conditional-notify / failure-template scenarios.

    pytest tests/test_tasks.py
    python tests/test_tasks.py
"""

import json
import os
import tempfile
import time

from apps.fileflow.core.steps import render_rename
from apps.fileflow.core.tasks import (
    TaskError,
    build_pipeline,
    validate_task,
)
from apps.fileflow.infra.demo import DemoProcessor, demo_mappings, ensure_demo_source
from apps.fileflow.scheduling.scheduler import Scheduler
from tests.flow.helpers import (
    make_engine,
    open_registry,
    outbox_texts,
    registry_for,
    wait_approval,
    wait_status,
)


def test_render_rename():
    out = render_rename(
        "{date}_{task}_{name}{ext}",
        "input_report.csv",
        sha256="ab" * 32,
        run_id=7,
        task_id="reports",
    )
    assert out.endswith("_reports_input_report.csv")
    assert render_rename("{checksum8}_{name}{ext}", "a.csv", sha256="ab" * 32) == "abababab_a.csv"
    try:
        render_rename("{nope}_{name}{ext}", "a.csv")
        raise AssertionError("unknown token accepted")
    except KeyError:
        pass


def test_validate_task():
    good = {
        "id": "ok-task",
        "name": "OK",
        "source": {"type": "website", "url": "https://example.com/a.csv"},
        "rename": "{date}_{name}{ext}",
        "destinations": [{"type": "local", "dir": "out"}],
    }
    validate_task(good)
    bad_cases = [
        ({"id": "Bad ID", "name": "x"}, "bad id"),
        ({"id": "ok", "name": "  "}, "empty name"),
        ({"id": "ok", "name": "x", "steps": [{"type": "bogus"}]}, "unknown step"),
        ({"id": "ok", "name": "x", "steps": [{"type": "rename"}]}, "steps without fetch"),
        (
            {
                "id": "ok",
                "name": "x",
                "steps": [{"type": "fetch"}, {"type": "copy", "to": {"type": "s3", "dir": "b"}}],
            },
            "bad copy target",
        ),
        (
            {
                "id": "ok",
                "name": "x",
                "steps": [{"type": "fetch"}, {"type": "copy", "to": {"type": "local", "dir": ""}}],
            },
            "empty copy dir",
        ),
        ({"id": "ok", "name": "x", "rename": "{date}/{name}{ext}"}, "rename with separator"),
        ({"id": "ok", "name": "x", "source": {"type": "website"}}, "website without url"),
        ({"id": "ok", "name": "x", "run_limit": {"max": 0}}, "limit max 0"),
        ({"id": "ok", "name": "x", "run_limit": {"max": "3"}}, "limit max string"),
        ({"id": "ok", "name": "x", "run_limit": {"max": 3, "period": "year"}}, "limit bad period"),
        ({"id": "ok", "name": "x", "run_limit": "3"}, "limit not an object"),
    ]
    for task, why in bad_cases:
        try:
            validate_task(task)
            raise AssertionError(f"validate accepted: {why}")
        except TaskError:
            pass
    try:
        validate_task(good, existing_ids=["ok-task"])
        raise AssertionError("duplicate id accepted")
    except TaskError:
        pass


def test_shipped_task_files_validate():
    """Every task shipped in the repo must pass Pydantic validation —
    the models may not be stricter than the tasks we actually ship."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in ("tasks.json", "tasks.scenarios.json", "tasks.example.json"):
        doc = json.load(open(os.path.join(root, name)))
        ids = [t["id"] for t in doc["tasks"]]
        assert len(ids) == len(set(ids)), f"{name} has duplicate ids"
        for task in doc["tasks"]:
            validate_task(task, [x for x in ids if x != task["id"]])


def test_build_pipeline_shorthand():
    task = {
        "id": "t",
        "name": "T",
        "approval": True,
        "rename": "{date}_{name}{ext}",
        "destinations": [
            {"type": "ftp", "dir": "inbox"},
            {"type": "local", "dir": "arc"},
            {"type": "smb", "dir": "share"},
        ],
        "processing": {"dir": "processed", "pattern": "{renamed_name}"},
        "notify": True,
    }
    keys = [k for k, _l, _s, _p in build_pipeline(task)]
    assert keys == [
        "fetch",
        "checksum",
        "approval",
        "rename",
        "copy:ftp:inbox",
        "wait_processed",
        "fetch_processed",
        "copy:local:arc",
        "copy:smb:share",
        "notify",
    ], keys
    # no processing: copies in declared order, no wait/fetch_processed
    task2 = {
        "id": "t2",
        "name": "T2",
        "approval": False,
        "rename": "{name}{ext}",
        "destinations": [{"type": "local", "dir": "a"}, {"type": "local", "dir": "b"}],
        "notify": False,
    }
    keys2 = [k for k, _l, _s, _p in build_pipeline(task2)]
    assert keys2 == ["fetch", "checksum", "rename", "copy:local:a", "copy:local:b"], keys2


def test_registry_crud_and_reload():
    with tempfile.TemporaryDirectory() as tmp:
        _, reg = registry_for(tmp, [])
        assert reg.all() == []
        task = {
            "id": "a",
            "name": "A",
            "approval": False,
            "rename": "{name}{ext}",
            "destinations": [{"type": "local", "dir": "a"}],
            "notify": False,
        }
        reg.create(task)
        assert reg.get("a")["name"] == "A"
        # persisted: a fresh registry over the same file sees it
        _, reg2 = open_registry(tmp)
        assert reg2.get("a") is not None
        # external edit is picked up (mtime reload)
        with open(os.path.join(tmp, "tasks.json"), "w", encoding="utf-8") as f:
            json.dump({"tasks": [dict(task, name="A-renamed")]}, f)
        future = time.time() + 5
        os.utime(os.path.join(tmp, "tasks.json"), (future, future))
        assert reg.get("a")["name"] == "A-renamed"
        # update + disable + delete
        reg.update("a", dict(task, name="A2"))
        assert reg.get("a")["name"] == "A2"
        reg.set_enabled("a", False)
        assert reg.get("a")["enabled"] is False
        reg.delete("a")
        assert reg.get("a") is None
        try:
            reg.delete("a")
            raise AssertionError("double delete accepted")
        except TaskError:
            pass


def test_demo_mappings():
    tasks = [
        {
            "id": "inv",
            "name": "Inv",
            "approval": False,
            "rename": "{name}{ext}",
            "destinations": [{"type": "ftp", "dir": "invoices-inbox"}],
            "processing": {"dir": "invoices-processed", "pattern": "{renamed_name}"},
            "notify": False,
        },
        {"id": "plain", "name": "Plain", "approval": False, "notify": False},
    ]
    mapping = demo_mappings(tasks)
    assert mapping["inbox"] == "processed"
    assert mapping["invoices-inbox"] == "invoices-processed"


def test_multi_approval_gates():
    """Three approval steps must each really wait — a leaked decision
    from the first gate must not auto-pass the later ones."""
    task = {
        "id": "multi",
        "name": "Multi",
        "steps": [
            {"type": "fetch"},
            {"type": "approval"},
            {"type": "checksum"},
            {"type": "rename", "pattern": "{date}_{name}{ext}"},
            {"type": "approval"},
            {"type": "copy", "to": {"type": "local", "dir": "multi"}},
            {"type": "approval"},
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        cfg, db, engine, _reg = make_engine(tmp, [task])
        ensure_demo_source(cfg)
        run_id = engine.create_and_start("multi")
        for key in ("approval", "approval:2", "approval:3"):
            wait_approval(db, run_id, key)
            assert engine.approve(run_id)
        wait_status(db, run_id, "COMPLETED")
        assert os.path.exists(os.path.join(cfg.path("multi"), os.listdir(cfg.path("multi"))[0]))


def test_no_destination_no_processing():
    task = {
        "id": "bare",
        "name": "Bare",
        "steps": [
            {"type": "fetch"},
            {"type": "checksum"},
            {"type": "rename", "pattern": "{date}_{name}{ext}"},
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        cfg, db, engine, _reg = make_engine(tmp, [task])
        ensure_demo_source(cfg)
        run_id = engine.create_and_start("bare")
        run = wait_status(db, run_id, "COMPLETED")
        renamed = run["context"]["renamed_path"]
        assert os.path.exists(renamed)


def test_process_before_rename():
    task = {
        "id": "pre",
        "name": "Pre",
        "steps": [
            {"type": "fetch"},
            {"type": "checksum"},
            {"type": "copy", "to": {"type": "ftp", "dir": "inbox"}},
            {"type": "wait_processed", "dir": "processed", "pattern": "{current_name}"},
            {"type": "fetch_processed"},
            {"type": "rename", "pattern": "PROC-{date}_{name}{ext}"},
            {"type": "copy", "to": {"type": "local", "dir": "processed-out"}},
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        cfg, db, engine, _reg = make_engine(tmp, [task])
        ensure_demo_source(cfg)
        DemoProcessor(cfg, lambda run_id, msg, level="INFO": None).start()
        run_id = engine.create_and_start("pre")
        wait_status(db, run_id, "COMPLETED")
        names = os.listdir(cfg.path("processed-out"))
        assert len(names) == 1 and names[0].startswith("PROC-"), names
        content = open(os.path.join(cfg.path("processed-out"), names[0]), encoding="utf-8").read()
        assert "PROCESSED" in content


def test_conditional_notify():
    task = {
        "id": "cond",
        "name": "Cond",
        "steps": [
            {"type": "fetch"},
            {"type": "checksum"},
            {"type": "rename", "pattern": "{date}_{name}{ext}"},
            {"type": "copy", "to": {"type": "local", "dir": "cond"}},
            {
                "type": "notify",
                "subject": "big file",
                "template": "big",
                "when": {"field": "downloaded_size", "op": "gt", "value": 1000000},
            },
            {
                "type": "notify",
                "subject": "done file",
                "template": "done {current_name}",
                "when": {"field": "sha256", "op": "exists"},
            },
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        cfg, db, engine, _reg = make_engine(tmp, [task])
        ensure_demo_source(cfg)
        run_id = engine.create_and_start("cond")
        wait_status(db, run_id, "COMPLETED")
        texts = outbox_texts(cfg)
        assert len(texts) == 1, texts
        assert "done file" in texts[0] and "big file" not in texts[0]
        steps = {s["step_key"]: s for s in db.list_steps(run_id)}
        assert "skipped" in (steps["notify"]["message"] or "")


def test_failure_templates():
    # task-level template, on a fetch failure
    task_a = {
        "id": "fail-a",
        "name": "Fail A",
        "source": {"type": "local", "path": "/nonexistent/nope.csv"},
        "failure_subject": "TASK-LEVEL-FAILURE {task} {step}",
        "failure_template": "TASK-LEVEL-FAILURE body {error}",
        "steps": [{"type": "fetch"}],
    }
    with tempfile.TemporaryDirectory() as tmp:
        cfg, db, engine, _reg = make_engine(tmp, [task_a])
        run_id = engine.create_and_start("fail-a")
        wait_status(db, run_id, "STUCK")
        texts = outbox_texts(cfg)
        assert len(texts) == 1 and "TASK-LEVEL-FAILURE" in texts[0], texts
    # step-level template overrides the task-level one, on a copy failure
    task_b = {
        "id": "fail-b",
        "name": "Fail B",
        "failure_template": "TASK-LEVEL-SHOULD-NOT-APPEAR",
        "steps": [
            {"type": "fetch"},
            {"type": "checksum"},
            {"type": "rename", "pattern": "{name}{ext}"},
            {
                "type": "copy",
                "to": {"type": "local", "dir": "blocker"},
                "failure_template": "COPY-STEP-FAILURE {error}",
            },
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        cfg, db, engine, _reg = make_engine(tmp, [task_b])
        ensure_demo_source(cfg)
        # a FILE named blocker makes makedirs fail inside the copy step
        open(cfg.path("blocker"), "w", encoding="utf-8").write("x")
        run_id = engine.create_and_start("fail-b")
        wait_status(db, run_id, "STUCK")
        texts = outbox_texts(cfg)
        assert len(texts) == 1, texts
        assert "COPY-STEP-FAILURE" in texts[0], texts
        assert "TASK-LEVEL-SHOULD-NOT-APPEAR" not in texts[0], texts


def test_approval_after_shorthand():
    task = {
        "id": "sh",
        "name": "Sh",
        "approval_after": ["rename", "copy"],
        "rename": "{name}{ext}",
        "destinations": [{"type": "local", "dir": "a"}, {"type": "local", "dir": "b"}],
        "notify": False,
    }
    keys = [k for k, _l, _s, _p in build_pipeline(task)]
    assert keys == [
        "fetch",
        "checksum",
        "rename",
        "approval",
        "copy:local:a",
        "approval:2",
        "copy:local:b",
        "approval:3",
    ], keys
    # a single copy key matches only that copy
    task2 = dict(task, approval_after=["copy:local:b"])
    keys2 = [k for k, _l, _s, _p in build_pipeline(task2)]
    assert keys2 == ["fetch", "checksum", "rename", "copy:local:a", "copy:local:b", "approval"], (
        keys2
    )
    # a point matching nothing is rejected
    try:
        validate_task(dict(task, approval_after=["nope"]))
        raise AssertionError("bad approval_after point accepted")
    except TaskError:
        pass


def test_scenarios_file_valid():
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(repo, "tasks.scenarios.json"), encoding="utf-8") as f:
        doc = json.load(f)
    ids = [t["id"] for t in doc["tasks"]]
    assert len(ids) >= 8
    for t in doc["tasks"]:
        validate_task(t, [x for x in ids if x != t["id"]])
        pipeline = build_pipeline(t)
        assert pipeline and pipeline[0][2] == "fetch"
    by_id = {t["id"]: t for t in doc["tasks"]}
    multi = build_pipeline(by_id["scenario-multi-approval"])
    assert sum(1 for _k, _l, s, _p in multi if s == "approval") == 3
    bare = build_pipeline(by_id["scenario-no-destination"])
    assert all(s != "copy" for _k, _l, s, _p in bare)


def test_scheduler_resolve_task():
    """A schedule fires exactly its task_id — no name matching, no
    'default' fallback, no first-enabled fallback."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg, reg = registry_for(
            tmp,
            [
                {"id": "default", "name": "Default", "approval": False, "notify": False},
                {"id": "other", "name": "Other", "approval": False, "notify": False},
            ],
        )
        sched = Scheduler(cfg, None, None, reg)
        assert sched.resolve_task({"task_id": "other"})["id"] == "other"
        assert sched.resolve_task({"task_id": "no-such-task"}) is None
        assert sched.resolve_task({"name": "Other"}) is None
        assert sched.resolve_task({}) is None
        assert Scheduler(cfg, None, None, None).resolve_task({"task_id": "other"}) is None


if __name__ == "__main__":
    # Standalone runner (no pytest needed): runs every test_* function.
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"{name}: OK")
    print("\nALL TESTS PASSED")
