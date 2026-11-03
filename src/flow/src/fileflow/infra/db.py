# FileFlow — SQLite persistence via SQLAlchemy ORM: runs, step runs,
# logs, audit trail, schedules.
#
# Tables and columns match the original raw-SQL schema exactly, so
# database files created by earlier versions keep working untouched.
# Each method opens a short-lived session (thread-safe: the engine,
# scheduler, and HTTP handlers all share one DB) and returns plain
# dicts, so callers never see ORM objects.
import json
import threading
from datetime import datetime, timezone

from sqlalchemy import Integer, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    task_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="PENDING")
    current_step: Mapped[str | None] = mapped_column(Text, nullable=True)
    context: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class StepRun(Base):
    __tablename__ = "step_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    step_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[str | None] = mapped_column(Text, nullable=True)


class Log(Base):
    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    ts: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[str] = mapped_column(Text, nullable=False, default="INFO")
    message: Mapped[str] = mapped_column(Text, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    task_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    frequency: Mapped[str] = mapped_column(Text, nullable=False)
    time_of_day: Mapped[str] = mapped_column(Text, nullable=False)
    weekday: Mapped[int | None] = mapped_column(Integer, nullable=True)
    day_of_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_run_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_run_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _row_dict(row):
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


class DB:
    def __init__(self, path):
        self.engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self._Session = sessionmaker(bind=self.engine)
        self._lock = threading.Lock()

    # ---- runs -----------------------------------------------------------
    def create_run(self, name, task_id):
        ts = now_iso()
        with self._lock, self._Session() as s:
            row = Run(name=name, task_id=task_id, status="PENDING", created_at=ts, updated_at=ts)
            s.add(row)
            s.commit()
            return row.id

    def get_run(self, run_id):
        with self._lock, self._Session() as s:
            row = s.get(Run, run_id)
            if row is None:
                return None
            d = _row_dict(row)
        d["context"] = json.loads(d["context"] or "{}")
        return d

    def list_runs(self):
        with self._lock, self._Session() as s:
            rows = s.execute(select(Run).order_by(Run.id.desc())).scalars().all()
            out = [_row_dict(r) for r in rows]
        for d in out:
            d["context"] = json.loads(d["context"] or "{}")
        return out

    def update_run(self, run_id, **fields):
        if "context" in fields:
            fields["context"] = json.dumps(fields["context"])
        fields["updated_at"] = now_iso()
        with self._lock, self._Session() as s:
            row = s.get(Run, run_id)
            if row is None:
                return
            for k, v in fields.items():
                setattr(row, k, v)
            s.commit()

    def active_run_for_task(self, task_id, statuses):
        """The task's active run, if any (newest first)."""
        with self._lock, self._Session() as s:
            row = s.execute(
                select(Run.id, Run.status)
                .where(Run.task_id == task_id, Run.status.in_(statuses))
                .order_by(Run.id.desc())
                .limit(1)
            ).first()
        return {"id": row[0], "status": row[1]} if row is not None else None

    def runs_for_task(self, task_id):
        """(id, status, created_at) for every run of a task, oldest first."""
        with self._lock, self._Session() as s:
            rows = s.execute(
                select(Run.id, Run.status, Run.created_at)
                .where(Run.task_id == task_id)
                .order_by(Run.id)
            ).all()
        return [{"id": r[0], "status": r[1], "created_at": r[2]} for r in rows]

    # ---- step runs ------------------------------------------------------
    def start_step(self, run_id, step_key, attempt):
        with self._lock, self._Session() as s:
            row = StepRun(
                run_id=run_id,
                step_key=step_key,
                status="RUNNING",
                attempt=attempt,
                started_at=now_iso(),
            )
            s.add(row)
            s.commit()
            return row.id

    def finish_step(self, step_run_id, status, message=None):
        with self._lock, self._Session() as s:
            row = s.get(StepRun, step_run_id)
            if row is None:
                return
            row.status = status
            row.message = message
            row.finished_at = now_iso()
            s.commit()

    def list_steps(self, run_id):
        with self._lock, self._Session() as s:
            rows = (
                s.execute(select(StepRun).where(StepRun.run_id == run_id).order_by(StepRun.id))
                .scalars()
                .all()
            )
            return [_row_dict(r) for r in rows]

    def attempt_count(self, run_id, step_key):
        with self._lock, self._Session() as s:
            rows = (
                s.execute(
                    select(StepRun.id).where(StepRun.run_id == run_id, StepRun.step_key == step_key)
                )
                .scalars()
                .all()
            )
            return len(rows)

    # ---- logs & audit ---------------------------------------------------
    def add_log(self, run_id, message, level="INFO"):
        with self._lock, self._Session() as s:
            row = Log(run_id=run_id, ts=now_iso(), level=level, message=message)
            s.add(row)
            s.commit()
            return row.id

    def get_logs(self, run_id, after_id=0, limit=500):
        with self._lock, self._Session() as s:
            rows = (
                s.execute(
                    select(Log)
                    .where(Log.run_id == run_id, Log.id > after_id)
                    .order_by(Log.id)
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            return [_row_dict(r) for r in rows]

    def audit(self, actor, action, detail=""):
        with self._lock, self._Session() as s:
            s.add(AuditLog(ts=now_iso(), actor=actor, action=action, detail=detail))
            s.commit()

    # ---- schedules ------------------------------------------------------
    def create_schedule(
        self,
        name,
        frequency,
        time_of_day,
        weekday=None,
        day_of_month=None,
        enabled=True,
        next_run_at=None,
        task_id=None,
    ):
        ts = now_iso()
        with self._lock, self._Session() as s:
            row = Schedule(
                name=name,
                task_id=task_id,
                frequency=frequency,
                time_of_day=time_of_day,
                weekday=weekday,
                day_of_month=day_of_month,
                enabled=1 if enabled else 0,
                next_run_at=next_run_at,
                created_at=ts,
                updated_at=ts,
            )
            s.add(row)
            s.commit()
            return row.id

    def get_schedule(self, sid):
        with self._lock, self._Session() as s:
            row = s.get(Schedule, sid)
            if row is None:
                return None
            d = _row_dict(row)
        d["enabled"] = bool(d["enabled"])
        return d

    def list_schedules(self):
        with self._lock, self._Session() as s:
            rows = s.execute(select(Schedule).order_by(Schedule.id)).scalars().all()
            out = [_row_dict(r) for r in rows]
        for d in out:
            d["enabled"] = bool(d["enabled"])
        return out

    def update_schedule(self, sid, **fields):
        fields["updated_at"] = now_iso()
        if "enabled" in fields:
            fields["enabled"] = 1 if fields["enabled"] else 0
        with self._lock, self._Session() as s:
            row = s.get(Schedule, sid)
            if row is None:
                return
            for k, v in fields.items():
                setattr(row, k, v)
            s.commit()

    def delete_schedule(self, sid):
        with self._lock, self._Session() as s:
            row = s.get(Schedule, sid)
            if row is not None:
                s.delete(row)
                s.commit()
