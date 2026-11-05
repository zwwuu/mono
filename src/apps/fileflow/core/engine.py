# FileFlow — run engine.
#
# One runner thread per active run. A run executes one task's pipeline
# (see core/tasks.py): steps run sequentially; each step executes in a
# child thread joined with a timeout so a hung step is detected as STUCK
# instead of blocking forever. On STUCK the engine emails the configured
# recipients and waits for a human to press Retry on the dashboard (or
# Reject to fail the run at the approval gate).
import threading
from datetime import datetime, timedelta

from common.logging import log
from common.pipeline import steps as steps_mod
from common.pipeline.builder import build_pipeline
from common.pipeline.notify import send_email

STEP_TIMEOUT_DEFAULT = 300  # seconds per step unless config overrides

# A run in any of these states counts as "the task is already running":
# a second run of the same task is refused until the active one finishes,
# is rejected at an approval gate, is abandoned from STUCK, or fails.
ACTIVE_STATUSES = ("PENDING", "RUNNING", "AWAITING_APPROVAL", "STUCK")


class AlreadyRunningError(Exception):
    """Raised when a task already has an active run."""

    def __init__(self, task_id, run_id, status):
        super().__init__(f"task '{task_id}' already has an active run #{run_id} ({status})")
        self.task_id = task_id
        self.run_id = run_id
        self.status = status


class RunLimitExceeded(Exception):
    """Raised when a task reached its configured run limit."""

    def __init__(self, task_id, used, maximum, period):
        super().__init__(f"task '{task_id}' reached its run limit ({used}/{maximum} per {period})")
        self.task_id = task_id
        self.used = used
        self.maximum = maximum
        self.period = period


def period_start(period, now):
    """Start of the current local day/week (Mon)/month; None for total."""
    if period == "day":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "week":
        return (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    if period == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return None


def count_runs_in_period(db, task_id, period, now=None):
    """Runs created in the current period (all statuses count — a
    started run consumed a slot even if it later failed or was
    rejected). Local server time, like schedule times."""
    if now is None:
        now = datetime.now().astimezone()
    start = period_start(period, now)
    if start is None:
        return len(db.runs_for_task(task_id))
    count = 0
    for run in db.runs_for_task(task_id):
        try:
            created = datetime.fromisoformat(run["created_at"])
        except (TypeError, ValueError) as _e:
            continue
        if created >= start:
            count += 1
    return count


class LogBus:
    """Fan-out for live log streaming (SSE)."""

    def __init__(self):
        self._subs = {}
        self._lock = threading.Lock()

    def subscribe(self, run_id):
        q = []
        with self._lock:
            self._subs.setdefault(run_id, []).append(q)
        return q

    def unsubscribe(self, run_id, q):
        with self._lock:
            if run_id in self._subs and q in self._subs[run_id]:
                self._subs[run_id].remove(q)

    def publish(self, run_id, entry):
        with self._lock:
            targets = list(self._subs.get(run_id, []))
        for q in targets:
            q.append(entry)


class Engine:
    def __init__(self, cfg, db, registry=None):
        self.cfg = cfg
        self.db = db
        self.registry = registry
        self.bus = LogBus()
        self._approval_events = {}  # run_id -> threading.Event
        self._approval_decision = {}  # run_id -> "approved" | "rejected"
        self._lock = threading.Lock()

    # ------------------------------------------------------------- logging
    def log(self, run_id, message, level="INFO"):
        log_id = self.db.add_log(run_id, message, level)
        entry = {"id": log_id, "run_id": run_id, "level": level, "message": message}
        self.bus.publish(run_id, entry)
        # Mirror to the app logger (console + date-rotated file). The DB
        # row stays the dashboard's source of truth; this is the
        # operator's tail-able transcript.
        log_fn = getattr(log, level.lower(), log.info)
        log_fn(f"[run #{run_id}] {message}")
        return entry

    # ------------------------------------------------------------ lifecycle
    def create_and_start(self, task_id, actor="dashboard", allow_over_limit=False):
        task = self.registry.get(task_id) if self.registry else None
        if task is None:
            raise ValueError(f"unknown task: {task_id!r}")
        # Check-and-create is atomic: two callers racing to start the
        # same task must not both slip through.
        with self._lock:
            active = self.db.active_run_for_task(task_id, ACTIVE_STATUSES)
            if active is not None:
                raise AlreadyRunningError(task_id, active["id"], active["status"])
            limit = task.get("run_limit")
            if limit:
                period = limit.get("period", "day")
                used = count_runs_in_period(self.db, task_id, period)
                if used >= int(limit["max"]):
                    if not allow_over_limit:
                        raise RunLimitExceeded(task_id, used, limit["max"], period)
                    self.db.audit(
                        actor,
                        "override_limit",
                        f"run for task '{task_id}' over limit ({used}/{limit['max']} per {period})",
                    )
            run_id = self.db.create_run(task["name"], task_id=task_id)
            self.db.audit(actor, "create", f"run #{run_id} task '{task_id}'")
            self.log(run_id, f"Run created: task '{task_id}' ({task['name']})")
        self._launch(run_id, from_step=0)
        return run_id

    def _task_for(self, run_id):
        run = self.db.get_run(run_id)
        if run is None:
            return None, None
        task = self.registry.get(run.get("task_id")) if self.registry else None
        return run, task

    def _launch(self, run_id, from_step):
        threading.Thread(target=self._run, args=(run_id, from_step), daemon=True).start()

    def _run(self, run_id, from_step):
        run, task = self._task_for(run_id)
        if task is None:
            self.db.update_run(run_id, status="FAILED", error="task was removed")
            self.log(run_id, "Run FAILED: its task was removed", "ERROR")
            return
        self.db.update_run(run_id, status="RUNNING", error=None)
        step_list = build_pipeline(task)
        for idx in range(from_step, len(step_list)):
            key, label, _stype, params = step_list[idx]
            run = self.db.get_run(run_id)
            if run["status"] in ("FAILED", "COMPLETED"):
                return
            if key.startswith("approval"):
                if not self._approval_gate(run_id, key):
                    self.db.update_run(run_id, status="FAILED", error="rejected by operator")
                    self.log(run_id, "Run rejected by operator", "WARN")
                    self.db.audit("operator", "reject", f"run #{run_id}")
                    return
            ok = self._run_step(run_id, idx, key, label, step_list[idx], task, len(step_list))
            if not ok:
                return  # STUCK — wait for retry() from the dashboard
        self.db.update_run(run_id, status="COMPLETED", current_step=None)
        self.log(run_id, "Run COMPLETED")
        self.db.audit("engine", "complete", f"run #{run_id}")

    def _run_step(self, run_id, idx, key, label, step_entry, task, total):
        _key, _label, stype, params = step_entry
        fn = steps_mod.STEP_FUNCS[stype]
        run = self.db.get_run(run_id)
        attempt = self.db.attempt_count(run_id, key) + 1
        step_run_id = self.db.start_step(run_id, key, attempt)
        self.db.update_run(run_id, current_step=key)
        self.log(run_id, f"Step {idx + 1}/{total} [{key}] {label} (attempt {attempt})")

        ctx = steps_mod.StepContext(
            self.cfg,
            run,
            lambda m, level="INFO": self.log(run_id, m, level),
            task,
            params,
            key,
        )
        timeout = int(
            params.get(
                "timeout_seconds",
                self.cfg.get(
                    "steps",
                    f"{stype}_timeout_seconds",
                    self.cfg.get("steps", "timeout_seconds", STEP_TIMEOUT_DEFAULT),
                ),
            )
        )
        result = {}

        def target():
            try:
                result["msg"] = fn(ctx)
            except Exception as e:  # noqa: BLE001 — surfaced as STUCK below
                result["error"] = e

        th = threading.Thread(target=target, daemon=True)
        th.start()
        th.join(timeout)

        # persist whatever context the step produced
        self.db.update_run(run_id, context=ctx.data)

        if th.is_alive():
            msg = f"step '{key}' exceeded timeout of {timeout}s"
            self.db.finish_step(step_run_id, "STUCK", msg)
            self._mark_stuck(run_id, key, msg, task, params)
            return False
        if "error" in result:
            err = result["error"]
            msg = f"{type(err).__name__}: {err}"
            self.db.finish_step(step_run_id, "STUCK", msg)
            self.log(run_id, f"Step [{key}] failed: {msg}", "ERROR")
            self._mark_stuck(run_id, key, msg, task, params)
            return False
        self.db.finish_step(step_run_id, "SUCCESS", result.get("msg"))
        self.log(run_id, f"Step [{key}] OK: {result.get('msg')}")
        return True

    # -------------------------------------------------------------- approval
    def _approval_gate(self, run_id, key="approval"):
        """Block until the operator approves/rejects at this gate.

        Each gate consumes exactly one decision: the decision is popped
        under lock, so a task with several approval steps really waits at
        every one of them instead of auto-passing after the first.
        """
        self.db.update_run(run_id, status="AWAITING_APPROVAL", current_step=key)
        self.log(run_id, f"Waiting for human approval on the dashboard (step [{key}])…", "WARN")
        ev = threading.Event()
        with self._lock:
            self._approval_events[run_id] = ev
            decision = self._approval_decision.pop(run_id, None)
        if decision is None:
            ev.wait()  # released by approve()/reject()
            with self._lock:
                decision = self._approval_decision.pop(run_id, "approved")
        self.db.update_run(run_id, status="RUNNING")
        return decision == "approved"

    def approve(self, run_id, actor="dashboard"):
        with self._lock:
            self._approval_decision[run_id] = "approved"
            ev = self._approval_events.get(run_id)
        self.db.audit(actor, "approve", f"run #{run_id}")
        self.log(run_id, "Approved by operator")
        if ev:
            ev.set()
        return True

    def reject(self, run_id, actor="dashboard"):
        with self._lock:
            self._approval_decision[run_id] = "rejected"
            ev = self._approval_events.get(run_id)
        self.db.audit(actor, "reject", f"run #{run_id}")
        if ev:
            ev.set()
        return True

    # ---------------------------------------------------------------- stuck
    def _mark_stuck(self, run_id, key, msg, task=None, params=None):
        self.db.update_run(run_id, status="STUCK", current_step=key, error=msg)
        self.log(run_id, f"Run STUCK at step [{key}]: {msg}", "ERROR")
        self.db.audit("engine", "stuck", f"run #{run_id} step {key}: {msg}")
        if self.cfg.get("notify", "on_stuck", True):
            task = task or {}
            params = params or {}
            run = self.db.get_run(run_id) or {}
            d = run.get("context", {})
            tokens = {
                "task": task.get("id", ""),
                "task_name": task.get("name", ""),
                "run_id": str(run_id),
                "step": key,
                "error": msg,
                "current_name": d.get("current_name", ""),
                "current_path": d.get("current_path", ""),
                "downloaded_name": d.get("downloaded_name", ""),
                "downloaded_size": str(d.get("downloaded_size", "")),
                "sha256": d.get("sha256", ""),
                "renamed_name": d.get("renamed_name", ""),
                "processed_name": d.get("processed_name", ""),
                "processed_size": str(d.get("processed_size", "")),
                "processed_sha256": d.get("processed_sha256", ""),
            }
            # Precedence: failing step's own template, then the task's,
            # then the built-in default — so success and failure mail can
            # differ per task and per step.
            subject = steps_mod.render_template(
                params.get("failure_subject")
                or task.get("failure_subject")
                or steps_mod.DEFAULT_FAILURE_SUBJECT,
                tokens,
            )
            body = steps_mod.render_template(
                params.get("failure_template")
                or task.get("failure_template")
                or steps_mod.DEFAULT_FAILURE_TEMPLATE,
                tokens,
            )
            try:
                ok, how = send_email(self.cfg, task.get("recipients"), subject, body)
                self.log(run_id, f"Stuck notification email: {'sent' if ok else 'FAILED'} ({how})")
            except Exception as e:  # noqa: BLE001
                self.log(run_id, f"Stuck notification email error: {e}", "ERROR")

    def retry(self, run_id, actor="dashboard"):
        run = self.db.get_run(run_id)
        if run is None or run["status"] != "STUCK":
            return False
        _run, task = self._task_for(run_id)
        if task is None:
            return False
        step_keys = [s[0] for s in build_pipeline(task)]
        if run["current_step"] not in step_keys:
            self.db.update_run(
                run_id, status="FAILED", error=f"step {run['current_step']!r} no longer in task"
            )
            return False
        idx = step_keys.index(run["current_step"])
        self.db.audit(actor, "retry", f"run #{run_id} from step {run['current_step']}")
        self.log(run_id, f"Retry requested from step [{run['current_step']}]")
        self.db.update_run(run_id, status="RUNNING", error=None)
        self._launch(run_id, from_step=idx)
        return True

    def fail(self, run_id, actor="dashboard"):
        """Abandon a STUCK run so its task can run again."""
        run = self.db.get_run(run_id)
        if run is None or run["status"] != "STUCK":
            return False
        self.db.update_run(run_id, status="FAILED", error="abandoned by operator")
        self.db.audit(actor, "fail", f"run #{run_id}")
        self.log(run_id, "Run abandoned by operator", "WARN")
        return True
