"""Run ledger (SQLite): the single source of truth for correctness.

Every run is recorded once, keyed by an idempotency key::

    <job> + "|" + <run_id> + "|" + <input sha256>

If the same key is seen again (re-upload, watcher restart, crash retry)
the second attempt is recognised as a duplicate and is NOT re-processed
and NOT re-published. That is what makes the whole pipeline safe to
retry: a retry can never run a business procedure twice.

States: RECEIVED -> VERIFIED -> PROCESSING -> PROCESSED -> PUBLISHED
        RECEIVED/VERIFIED -> QUARANTINED (integrity failure)
        any state          -> FAILED     (procedure / publish failure,
                                          reason recorded, file kept)
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .common import utcnow_iso

STATES = (
    "RECEIVED",
    "VERIFIED",
    "PROCESSING",
    "PROCESSED",
    "PUBLISHED",
    "FAILED",
    "QUARANTINED",
    "DUPLICATE",
    # reverse flow (SMB -> FTP outgoing -> website)
    "OUTBOUND_READY",
    "UPLOADED_TO_WEBSITE",
)


class Ledger:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                job TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                input_sha256 TEXT,
                state TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def claim(self, *, run_id: str, job: str, idempotency_key: str,
              input_sha256: str | None = None) -> bool:
        """Record a new run. Returns False if this key was already seen."""
        now = utcnow_iso()
        try:
            self.conn.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)",
                (run_id, job, idempotency_key, input_sha256, "RECEIVED",
                 "{}", now, now),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def set_state(self, run_id: str, state: str, **detail) -> None:
        if state not in STATES:
            raise ValueError(f"unknown state {state!r}")
        row = self.conn.execute(
            "SELECT detail FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        merged = json.loads(row[0]) if row else {}
        merged.update(detail)
        self.conn.execute(
            "UPDATE runs SET state=?, detail=?, updated_at=? WHERE run_id=?",
            (state, json.dumps(merged), utcnow_iso(), run_id),
        )
        self.conn.commit()

    def get(self, run_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT run_id, job, idempotency_key, input_sha256, state, detail,"
            " created_at, updated_at FROM runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if not row:
            return None
        keys = ("run_id", "job", "idempotency_key", "input_sha256",
                "state", "detail", "created_at", "updated_at")
        d = dict(zip(keys, row))
        d["detail"] = json.loads(d["detail"])
        return d

    def seen_key(self, idempotency_key: str) -> dict | None:
        row = self.conn.execute(
            "SELECT run_id, state FROM runs WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
        return {"run_id": row[0], "state": row[1]} if row else None

    def list_by_state(self, state: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT run_id, job, state, updated_at FROM runs"
            " WHERE state=? ORDER BY updated_at",
            (state,),
        ).fetchall()
        return [{"run_id": r[0], "job": r[1], "state": r[2], "updated_at": r[3]}
                for r in rows]
