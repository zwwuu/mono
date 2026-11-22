"""Infrastructure -- persistence: the ledger on SQLAlchemy ORM.

Implements the domain's RunRepository port against SQLite. The
table layout is the one Alembic revision 001 creates, so a database
built by ``alembic upgrade head`` and one built here are identical.

``SqlAlchemyRunRepository`` speaks domain objects (RunRecord).
"""

from __future__ import annotations

import json
from pathlib import Path

from fileflow_contract import utcnow_iso
from sqlalchemy import String, Text, create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from ..domain.entities import STATES, RunRecord
from .filesystem import log


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    job: Mapped[str] = mapped_column(String, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    state: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)

    def to_record(self) -> RunRecord:
        return RunRecord(
            run_id=self.run_id,
            job=self.job,
            idempotency_key=self.idempotency_key,
            state=self.state,
            detail=json.loads(self.detail),
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class SqlAlchemyRunRepository:
    """RunRepository on SQLite. One session per operation; the engine
    is created once and disposed on close()."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{self.db_path}", connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(self.engine)

    def close(self) -> None:
        self.engine.dispose()

    def claim(self, *, run_id: str, job: str, idempotency_key: str) -> bool:
        """Record a new run. Returns False if this key was already seen."""
        now = utcnow_iso()
        with Session(self.engine) as session:
            session.add(
                Run(
                    run_id=run_id,
                    job=job,
                    idempotency_key=idempotency_key,
                    state="RECEIVED",
                    detail="{}",
                    created_at=now,
                    updated_at=now,
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                return False
        return True

    def set_state(self, run_id: str, state: str, **detail) -> None:
        if state not in STATES:
            raise ValueError(f"unknown state {state!r}")
        with Session(self.engine) as session:
            row = session.get(Run, run_id)
            if row is None:
                log.warning("set_state %s on unknown run %s; ignored", state, run_id)
                return
            merged = json.loads(row.detail)
            merged.update(detail)
            row.state = state
            row.detail = json.dumps(merged)
            row.updated_at = utcnow_iso()
            session.commit()

    def get(self, run_id: str) -> RunRecord | None:
        with Session(self.engine) as session:
            row = session.get(Run, run_id)
            return row.to_record() if row is not None else None

    def seen_key(self, idempotency_key: str) -> dict | None:
        with Session(self.engine) as session:
            row = session.scalar(select(Run).where(Run.idempotency_key == idempotency_key))
            return {"run_id": row.run_id, "state": row.state} if row else None

    def list_by_state(self, state: str) -> list[RunRecord]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(Run).where(Run.state == state).order_by(Run.updated_at)
            ).all()
            return [r.to_record() for r in rows]
