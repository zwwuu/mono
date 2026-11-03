# FileFlow — scheduler: run tasks on a schedule or on demand.
#
# Frequencies: daily, weekly (weekday 0=Mon..6=Sun), monthly (day 1-31,
# clamped to the last day of shorter months). Times are local server time.
# A background thread ticks every [scheduler] tick_seconds (default 20),
# fires any schedule whose next_run_at has arrived, and advances it to the
# following occurrence. If the server was down at the scheduled time, the
# schedule fires once when the server comes back (catch-up), not once per
# missed slot. Unless [scheduler] allow_overlap is true, a schedule will
# not start a new run while its previous run is still active.
import threading
from calendar import monthrange
from datetime import datetime, timedelta

from loguru import logger

from ..core.engine import ACTIVE_STATUSES, AlreadyRunningError, RunLimitExceeded

FREQUENCIES = ("daily", "weekly", "monthly")
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def parse_time(time_of_day):
    """'HH:MM' -> (hour, minute); raises ValueError on bad input."""
    try:
        hh_s, mm_s = str(time_of_day).split(":")
        hh, mm = int(hh_s), int(mm_s)
    except (ValueError, AttributeError) as _e:
        raise ValueError("time must be HH:MM") from _e
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError("time must be HH:MM within 00:00-23:59")
    return hh, mm


def validate_schedule(frequency, time_of_day, weekday=None, day_of_month=None):
    """Normalize and validate schedule fields; returns a dict of clean values."""
    frequency = str(frequency or "").lower()
    if frequency not in FREQUENCIES:
        raise ValueError(f"frequency must be one of {', '.join(FREQUENCIES)}")
    hh, mm = parse_time(time_of_day)
    out = {
        "frequency": frequency,
        "time_of_day": f"{hh:02d}:{mm:02d}",
        "weekday": None,
        "day_of_month": None,
    }
    if frequency == "weekly":
        if weekday is None or not (0 <= int(weekday) <= 6):
            raise ValueError("weekly schedules need weekday 0 (Mon) - 6 (Sun)")
        out["weekday"] = int(weekday)
    if frequency == "monthly":
        if day_of_month is None or not (1 <= int(day_of_month) <= 31):
            raise ValueError("monthly schedules need day_of_month 1-31")
        out["day_of_month"] = int(day_of_month)
    return out


def describe(s):
    """Human-readable summary, e.g. 'Weekly on Monday at 07:30'."""
    t = s["time_of_day"]
    if s["frequency"] == "daily":
        return f"Daily at {t}"
    if s["frequency"] == "weekly":
        return f"Weekly on {WEEKDAY_NAMES[s['weekday']]} at {t}"
    return f"Monthly on day {s['day_of_month']} at {t}"


def compute_next_run(frequency, time_of_day, weekday, day_of_month, after):
    """First occurrence strictly after `after` (a local datetime)."""
    hh, mm = parse_time(time_of_day)
    if frequency == "daily":
        cand = after.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if cand <= after:
            cand += timedelta(days=1)
        return cand
    if frequency == "weekly":
        cand = after.replace(hour=hh, minute=mm, second=0, microsecond=0)
        cand += timedelta(days=(int(weekday) - after.weekday()) % 7)
        if cand <= after:
            cand += timedelta(days=7)
        return cand
    if frequency == "monthly":
        year, month = after.year, after.month
        for _ in range(14):
            day = min(int(day_of_month), monthrange(year, month)[1])
            cand = after.replace(
                year=year, month=month, day=day, hour=hh, minute=mm, second=0, microsecond=0
            )
            if cand > after:
                return cand
            month += 1
            if month == 13:
                month, year = 1, year + 1
    raise ValueError(f"unknown frequency: {frequency}")


def _fmt(dt):
    return dt.isoformat(timespec="seconds")


class Scheduler:
    def __init__(self, cfg, db, engine, registry=None):
        self.cfg = cfg
        self.db = db
        self.engine = engine
        self.registry = registry
        self.tick = float(cfg.get("scheduler", "tick_seconds", 20))
        self.allow_overlap = bool(cfg.get("scheduler", "allow_overlap", False))
        self._stop = threading.Event()
        self._thread = None

    def resolve_task(self, sched):
        """Task a schedule fires: its task_id, nothing else."""
        if self.registry is None:
            return None
        return self.registry.get(sched.get("task_id"))

    # ------------------------------------------------------------- firing
    def fire(self, sched, actor, advance, allow_disabled=False, allow_over_limit=False):
        """Start a run for this schedule.

        Returns (run_id, note, limit_exceeded): run_id is None when the
        run was skipped, and limit_exceeded marks a run-limit skip (the
        dashboard offers an override for those, but never for an
        already-running skip)."""
        task = self.resolve_task(sched)
        if task is None:
            note = "skipped: schedule has no task (task_id missing or task removed)"
            self.db.audit(actor, "schedule_skip", f"schedule #{sched['id']}: {note}")
            return None, note, False
        if not task.get("enabled", True) and not allow_disabled:
            note = f"skipped: task '{task['id']}' is disabled"
            self.db.audit(actor, "schedule_skip", f"schedule #{sched['id']}: {note}")
            if advance:
                self._advance(sched)
            return None, note, False
        prev = sched.get("last_run_id")
        if prev and not self.allow_overlap:
            run = self.db.get_run(prev)
            if run and run["status"] in ACTIVE_STATUSES:
                note = f"skipped: previous run #{prev} still {run['status']}"
                self.db.audit(
                    actor, "schedule_skip", f"schedule #{sched['id']} '{sched['name']}': {note}"
                )
                if advance:
                    self._advance(sched)
                return None, note, False
        run_id = None
        try:
            run_id = self.engine.create_and_start(
                task["id"], actor=actor, allow_over_limit=allow_over_limit
            )
        except AlreadyRunningError as e:
            # Another run of this task is already active (started from
            # the dashboard, another schedule, or a manual run).
            note = f"skipped: {e}"
            self.db.audit(actor, "schedule_skip", f"schedule #{sched['id']}: {note}")
            if advance:
                self._advance(sched)
            return None, note, False
        except RunLimitExceeded as e:
            # Automatic runs never override a limit; a human can still
            # run the task from the dashboard with an override.
            note = f"skipped: {e}"
            self.db.audit(actor, "schedule_skip", f"schedule #{sched['id']}: {note}")
            if advance:
                self._advance(sched)
            return None, note, True
        now = datetime.now().astimezone()
        fields = {"last_run_at": _fmt(now), "last_run_id": run_id}
        if advance:
            fields["next_run_at"] = _fmt(
                compute_next_run(
                    sched["frequency"],
                    sched["time_of_day"],
                    sched["weekday"],
                    sched["day_of_month"],
                    now,
                )
            )
        self.db.update_schedule(sched["id"], **fields)
        self.db.audit(
            actor, "schedule_fire", f"schedule #{sched['id']} '{sched['name']}' -> run #{run_id}"
        )
        return run_id, "started", False

    def run_now(self, sid, actor="dashboard", allow_over_limit=False):
        """On-demand run; does not move the schedule's next_run_at."""
        sched = self.db.get_schedule(sid)
        if sched is None:
            return None, "schedule not found", False
        return self.fire(
            sched, actor, advance=False, allow_disabled=True, allow_over_limit=allow_over_limit
        )

    def _advance(self, sched):
        nxt = compute_next_run(
            sched["frequency"],
            sched["time_of_day"],
            sched["weekday"],
            sched["day_of_month"],
            datetime.now().astimezone(),
        )
        self.db.update_schedule(sched["id"], next_run_at=_fmt(nxt))

    # ------------------------------------------------------------- loop
    def start(self):
        # Fill in next_run_at for enabled schedules that lack one.
        now = datetime.now().astimezone()
        for sched in self.db.list_schedules():
            if sched["enabled"] and not sched["next_run_at"]:
                self.db.update_schedule(
                    sched["id"],
                    next_run_at=_fmt(
                        compute_next_run(
                            sched["frequency"],
                            sched["time_of_day"],
                            sched["weekday"],
                            sched["day_of_month"],
                            now,
                        )
                    ),
                )
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.wait(self.tick):
            try:
                self._check_due()
            except Exception:  # noqa: BLE001 — scheduler must never die on one bad row
                continue

    def _check_due(self):
        now = datetime.now().astimezone()
        for sched in self.db.list_schedules():
            if not sched["enabled"] or not sched["next_run_at"]:
                continue
            due = datetime.fromisoformat(sched["next_run_at"])
            if due <= now:
                run_id, note, _limited = self.fire(sched, actor="scheduler", advance=True)
                if run_id is not None:
                    logger.info(f"schedule #{sched['id']} '{sched['name']}' -> run #{run_id}")
                else:
                    logger.debug(f"schedule #{sched['id']} '{sched['name']}': {note}")
