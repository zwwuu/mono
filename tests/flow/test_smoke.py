#!/usr/bin/env python3
"""FileFlow end-to-end tests — runnable with pytest or standalone.

Covers: the generic task engine (two tasks with different rename schemas
and different destinations), the full happy path (fetch → checksum →
approval → rename → FTP → wait → download → archive → SMB → email),
the stuck path (processing timeout → STUCK + stuck email → Retry →
completes), and scheduling (due schedule fires, run-now, overlap guard).

    pytest tests/
    python tests/test_smoke.py
"""

import hashlib
import os
import tempfile
import threading
import time
from datetime import datetime

from apps.fileflow.core.engine import AlreadyRunningError, RunLimitExceeded, count_runs_in_period
from apps.fileflow.infra.demo import DemoProcessor, ensure_demo_source
from apps.fileflow.scheduling import scheduler as sched_mod
from apps.fileflow.scheduling.scheduler import Scheduler
from tests.flow.helpers import make_engine, wait_status

FULL_TASK = {
    "id": "smoke-full",
    "name": "smoke-full",
    "enabled": True,
    "approval": True,
    "rename": "{date}_{checksum8}_{name}{ext}",
    "destinations": [
        {"type": "ftp", "dir": "inbox"},
        {"type": "local", "dir": "__data_archive__"},
        {"type": "smb", "dir": ""},
    ],
    "processing": {"dir": "processed", "pattern": "{renamed_name}", "poll_seconds": 1},
    "notify": True,
}


def auto_approve(engine, db, run_id, stop):
    while not stop.is_set():
        run = db.get_run(run_id)
        if run and run["status"] == "AWAITING_APPROVAL":
            engine.approve(run_id)
            return
        time.sleep(0.2)


def outbox_subjects(cfg):
    out = []
    for name in os.listdir(cfg.path("outbox")):
        with open(cfg.path("outbox", name), encoding="utf-8") as f:
            for line in f:
                if line.startswith("Subject:"):
                    out.append(line.strip())
    return out


def test_next_run():
    nr = sched_mod.compute_next_run
    fri_0600 = datetime(2026, 10, 23, 6, 0)  # Friday
    fri_0800 = datetime(2026, 10, 23, 8, 0)
    assert nr("daily", "07:30", None, None, fri_0600) == datetime(2026, 10, 23, 7, 30)
    assert nr("daily", "07:30", None, None, fri_0800) == datetime(2026, 10, 24, 7, 30)
    # weekly: next Monday (weekday 0); same-day later time counts
    assert nr("weekly", "07:30", 0, None, fri_0600) == datetime(2026, 10, 26, 7, 30)
    assert nr("weekly", "07:30", 4, None, fri_0600) == datetime(2026, 10, 23, 7, 30)
    assert nr("weekly", "07:30", 4, None, fri_0800) == datetime(2026, 10, 30, 7, 30)
    # monthly: same month if still ahead, else next month; day clamps to month length
    assert nr("monthly", "07:30", None, 25, fri_0600) == datetime(2026, 10, 25, 7, 30)
    assert nr("monthly", "07:30", None, 15, fri_0600) == datetime(2026, 11, 15, 7, 30)
    assert nr("monthly", "07:30", None, 31, datetime(2026, 1, 31, 8, 0)) == datetime(
        2026, 2, 28, 7, 30
    )
    assert nr("monthly", "07:30", None, 31, datetime(2026, 12, 31, 8, 0)) == datetime(
        2027, 1, 31, 7, 30
    )
    # validation
    for bad in [
        ("hourly", "07:30", None, None),
        ("weekly", "07:30", None, None),
        ("monthly", "07:30", None, 32),
        ("daily", "25:00", None, None),
    ]:
        try:
            sched_mod.validate_schedule(*bad)
            raise AssertionError(f"validate accepted {bad}")
        except ValueError:
            pass


def test_happy_path():
    with tempfile.TemporaryDirectory() as tmp:
        cfg, db, engine, _reg = make_engine(tmp, [FULL_TASK])
        ensure_demo_source(cfg)
        DemoProcessor(cfg, lambda run_id, msg, level="INFO": None).start()
        run_id = engine.create_and_start("smoke-full")
        stop = threading.Event()
        t = threading.Thread(target=auto_approve, args=(engine, db, run_id, stop), daemon=True)
        t.start()
        run = wait_status(db, run_id, "COMPLETED")
        stop.set()
        ctx = run["context"]
        src = cfg.path("demo_source", "input_report.csv")
        expect = hashlib.sha256(open(src, "rb").read()).hexdigest()
        assert ctx["sha256"] == expect, "checksum mismatch"
        assert os.path.exists(os.path.join(cfg.path("archive"), ctx["current_name"])), (
            "archive file missing"
        )
        assert os.path.exists(os.path.join(cfg.path("demo_smb"), ctx["processed_name"])), (
            "smb file missing"
        )
        content = open(
            os.path.join(cfg.path("archive"), ctx["current_name"]), encoding="utf-8"
        ).read()
        assert "PROCESSED" in content, "processed transform missing"
        assert any("completed" in s for s in outbox_subjects(cfg)), "completion email missing"


def test_stuck_and_retry():
    task = dict(FULL_TASK)
    task["id"] = "smoke-stuck"
    task["name"] = "smoke-stuck"
    task["processing"] = dict(FULL_TASK["processing"], timeout_seconds=3)
    with tempfile.TemporaryDirectory() as tmp:
        cfg, db, engine, _reg = make_engine(tmp, [task])
        ensure_demo_source(cfg)
        # NOTE: processor NOT started -> wait_processed will time out
        run_id = engine.create_and_start("smoke-stuck")
        stop = threading.Event()
        t = threading.Thread(target=auto_approve, args=(engine, db, run_id, stop), daemon=True)
        t.start()
        run = wait_status(db, run_id, "STUCK")
        stop.set()
        assert run["current_step"] == "wait_processed", run["current_step"]
        assert any("STUCK" in s for s in outbox_subjects(cfg)), "stuck email missing"
        # now start the processor and retry from the dashboard action
        DemoProcessor(cfg, lambda wid_, msg, level="INFO": None).start()
        assert engine.retry(run_id), "retry refused"
        wait_status(db, run_id, "COMPLETED")


def test_many_tasks_different_destinations_and_renames():
    """The generic engine's core promise: several tasks, each with its
    own rename schema and its own copy destinations, added as JSON."""
    task_a = {
        "id": "reports",
        "name": "Reports",
        "enabled": True,
        "approval": False,
        "rename": "{date}_{name}{ext}",
        "destinations": [{"type": "local", "dir": "reports/daily"}],
        "notify": False,
    }
    task_b = {
        "id": "invoices",
        "name": "Invoices",
        "enabled": True,
        "approval": False,
        "rename": "INV-{date}_{checksum8}{ext}",
        "destinations": [
            {"type": "local", "dir": "invoices"},
            {"type": "smb", "dir": "invoices-share"},
        ],
        "notify": False,
    }
    with tempfile.TemporaryDirectory() as tmp:
        cfg, db, engine, registry = make_engine(tmp, [task_a, task_b])
        ensure_demo_source(cfg)
        src = cfg.path("demo_source", "input_report.csv")
        expect_sha = hashlib.sha256(open(src, "rb").read()).hexdigest()
        today = datetime.now().strftime("%Y%m%d")

        wid_a = engine.create_and_start("reports")
        wf_a = wait_status(db, wid_a, "COMPLETED")
        assert wf_a["context"]["renamed_name"] == f"{today}_input_report.csv"
        assert os.path.exists(
            os.path.join(cfg.path("reports/daily"), f"{today}_input_report.csv")
        ), "task A destination missing"

        wid_b = engine.create_and_start("invoices")
        wf_b = wait_status(db, wid_b, "COMPLETED")
        renamed_b = f"INV-{today}_{expect_sha[:8]}.csv"
        assert wf_b["context"]["renamed_name"] == renamed_b
        assert os.path.exists(os.path.join(cfg.path("invoices"), renamed_b))
        assert os.path.exists(os.path.join(cfg.path("demo_smb", "invoices-share"), renamed_b))

        # add + remove via the registry (the future add/remove path)
        registry.create(
            {
                "id": "extra",
                "name": "Extra",
                "enabled": True,
                "approval": False,
                "rename": "{name}{ext}",
                "destinations": [{"type": "local", "dir": "extra"}],
                "notify": False,
            }
        )
        assert registry.get("extra") is not None
        registry.delete("extra")
        assert registry.get("extra") is None


def test_scheduler():
    task = dict(FULL_TASK)
    task["id"] = "smoke-scheduled"
    task["name"] = "smoke-scheduled"
    with tempfile.TemporaryDirectory() as tmp:
        cfg, db, engine, registry = make_engine(tmp, [task])
        ensure_demo_source(cfg)
        DemoProcessor(cfg, lambda run_id, msg, level="INFO": None).start()
        past = sched_mod.datetime.now().astimezone() - sched_mod.timedelta(minutes=1)
        sid = db.create_schedule(
            "smoke-scheduled",
            "daily",
            "07:30",
            next_run_at=past.isoformat(timespec="seconds"),
            task_id="smoke-scheduled",
        )
        scheduler = Scheduler(cfg, db, engine, registry)
        scheduler.start()
        try:
            deadline = time.time() + 30
            run_id = None
            while time.time() < deadline:
                s = db.get_schedule(sid)
                if s["last_run_id"]:
                    run_id = s["last_run_id"]
                    break
                time.sleep(0.3)
            assert run_id, "scheduler never fired the due schedule"
            stop = threading.Event()
            t = threading.Thread(target=auto_approve, args=(engine, db, run_id, stop), daemon=True)
            t.start()
            wait_status(db, run_id, "COMPLETED")
            stop.set()
            s = db.get_schedule(sid)
            assert s["last_run_at"], "last_run_at not recorded"
            assert (
                sched_mod.datetime.fromisoformat(s["next_run_at"])
                > sched_mod.datetime.now().astimezone()
            ), "next_run_at not advanced into the future"

            # on-demand run + overlap guard: a second run_now while the first
            # is still active (waiting at the approval gate) must be skipped.
            wid2, note, _lim = scheduler.run_now(sid)
            assert wid2, f"run_now failed: {note}"
            wid3, note3, _lim3 = scheduler.run_now(sid)
            assert wid3 is None and "skipped" in note3, f"overlap guard failed: {wid3} {note3}"
            stop = threading.Event()
            t = threading.Thread(target=auto_approve, args=(engine, db, wid2, stop), daemon=True)
            t.start()
            wait_status(db, wid2, "COMPLETED")
            stop.set()
        finally:
            scheduler.stop()


def test_task_cannot_run_twice():
    """One active run per task: a second start is refused while the
    first is AWAITING_APPROVAL, a different task can run concurrently,
    and the task is free again once the first run completes."""
    task = dict(FULL_TASK)
    task["id"] = "once"
    task["name"] = "once"
    other = {
        "id": "other",
        "name": "other",
        "enabled": True,
        "approval": False,
        "rename": "{date}_{name}{ext}",
        "destinations": [{"type": "local", "dir": "other-out"}],
        "notify": False,
    }
    with tempfile.TemporaryDirectory() as tmp:
        cfg, db, engine, _reg = make_engine(tmp, [task, other])
        ensure_demo_source(cfg)
        DemoProcessor(cfg, lambda run_id_, msg, level="INFO": None).start()
        run1 = engine.create_and_start("once")
        wait_status(db, run1, "AWAITING_APPROVAL")
        try:
            engine.create_and_start("once")
            raise AssertionError("second run of an active task was allowed")
        except AlreadyRunningError as e:
            assert str(run1) in str(e), str(e)
        # a different task is unaffected by the first task's active run
        run_other = engine.create_and_start("other")
        wait_status(db, run_other, "COMPLETED")
        engine.approve(run1)
        wait_status(db, run1, "COMPLETED")
        # task is free again after completion
        run3 = engine.create_and_start("once")
        wait_status(db, run3, "AWAITING_APPROVAL")
        engine.approve(run3)
        wait_status(db, run3, "COMPLETED")


def test_stuck_run_blocks_and_abandon_frees():
    """A STUCK run still blocks its task; abandoning it frees the task."""
    task = dict(FULL_TASK)
    task["id"] = "wedged"
    task["name"] = "wedged"
    task["processing"] = dict(FULL_TASK["processing"], timeout_seconds=3)
    with tempfile.TemporaryDirectory() as tmp:
        cfg, db, engine, _reg = make_engine(tmp, [task])
        ensure_demo_source(cfg)
        # NOTE: processor NOT started -> wait_processed will time out
        run1 = engine.create_and_start("wedged")
        stop = threading.Event()
        t = threading.Thread(target=auto_approve, args=(engine, db, run1, stop), daemon=True)
        t.start()
        wait_status(db, run1, "STUCK")
        stop.set()
        try:
            engine.create_and_start("wedged")
            raise AssertionError("second run of a STUCK task was allowed")
        except AlreadyRunningError:
            pass
        assert engine.fail(run1), "abandon refused"
        wait_status(db, run1, "FAILED")
        # task is free again after the abandon
        run2 = engine.create_and_start("wedged")
        wait_status(db, run2, "AWAITING_APPROVAL")


def test_scheduler_skips_when_task_running():
    """A schedule whose task already has an active run is skipped
    (not crashed), and fires once the task is free."""
    task = dict(FULL_TASK)
    task["id"] = "sched-once"
    task["name"] = "sched-once"
    with tempfile.TemporaryDirectory() as tmp:
        cfg, db, engine, registry = make_engine(tmp, [task])
        ensure_demo_source(cfg)
        DemoProcessor(cfg, lambda run_id_, msg, level="INFO": None).start()
        scheduler = Scheduler(cfg, db, engine, registry)
        sid = db.create_schedule(
            "sched-once",
            "daily",
            "07:30",
            task_id="sched-once",
            next_run_at="2000-01-01T00:00:00",
        )
        run1 = engine.create_and_start("sched-once")
        wait_status(db, run1, "AWAITING_APPROVAL")
        run_id, note, _lim = scheduler.run_now(sid)
        assert run_id is None and "already has an active run" in note, (run_id, note)
        engine.approve(run1)
        wait_status(db, run1, "COMPLETED")
        run2, note2, _lim2 = scheduler.run_now(sid)
        assert run2, f"run_now failed after task freed: {note2}"
        wait_status(db, run2, "AWAITING_APPROVAL")
        engine.approve(run2)
        wait_status(db, run2, "COMPLETED")


def test_run_limit_blocks_and_override_frees():
    """A task with run_limit max 1/day runs once, refuses the second
    start, and runs again when the dashboard overrides the limit."""
    task = {
        "id": "limited",
        "name": "limited",
        "enabled": True,
        "approval": False,
        "rename": "{date}_{name}{ext}",
        "destinations": [{"type": "local", "dir": "limited-out"}],
        "notify": False,
        "run_limit": {"max": 1, "period": "day"},
    }
    with tempfile.TemporaryDirectory() as tmp:
        cfg, db, engine, _reg = make_engine(tmp, [task])
        ensure_demo_source(cfg)
        run1 = engine.create_and_start("limited")
        wait_status(db, run1, "COMPLETED")
        assert count_runs_in_period(db, "limited", "day") == 1
        try:
            engine.create_and_start("limited")
            raise AssertionError("run over the limit was allowed")
        except RunLimitExceeded as e:
            assert "1/1 per day" in str(e), str(e)
        run3 = engine.create_and_start("limited", allow_over_limit=True)
        wait_status(db, run3, "COMPLETED")
        assert count_runs_in_period(db, "limited", "day") == 2


def test_scheduler_skips_on_run_limit():
    """A schedule for a task at its run limit is skipped (and advanced
    on tick fires); a dashboard run-now with override still works."""
    task = {
        "id": "sched-limited",
        "name": "sched-limited",
        "enabled": True,
        "approval": False,
        "rename": "{date}_{name}{ext}",
        "destinations": [{"type": "local", "dir": "sched-limited-out"}],
        "notify": False,
        "run_limit": {"max": 1, "period": "day"},
    }
    with tempfile.TemporaryDirectory() as tmp:
        cfg, db, engine, registry = make_engine(tmp, [task])
        ensure_demo_source(cfg)
        scheduler = Scheduler(cfg, db, engine, registry)
        sid = db.create_schedule(
            "sched-limited",
            "daily",
            "07:30",
            task_id="sched-limited",
            next_run_at="2000-01-01T00:00:00",
        )
        run1 = engine.create_and_start("sched-limited")
        wait_status(db, run1, "COMPLETED")
        run_id, note, limited = scheduler.run_now(sid)
        assert run_id is None and limited and "run limit" in note, (run_id, note, limited)
        # a tick fire skips too, and still advances to the next occurrence
        sched = db.get_schedule(sid)
        run_id2, note2, limited2 = scheduler.fire(sched, "scheduler", advance=True)
        assert run_id2 is None and limited2, (run_id2, note2, limited2)
        assert db.get_schedule(sid)["next_run_at"] != "2000-01-01T00:00:00"
        # dashboard override works for run-now as well
        run3, note3, _ = scheduler.run_now(sid, allow_over_limit=True)
        assert run3, f"override run_now failed: {note3}"
        wait_status(db, run3, "COMPLETED")


if __name__ == "__main__":
    # Standalone runner (no pytest needed): runs every test_* function.
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"{name}: OK")
    print("\nALL TESTS PASSED")
