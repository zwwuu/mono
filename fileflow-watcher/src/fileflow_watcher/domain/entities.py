"""Domain entities: the objects the business rules are about.

A *run record* is one row of the ledger: a run's identity, state,
and recorded detail. It is a plain dataclass -- no database, no
I/O -- so the use cases can build and inspect it without touching
SQLite.

The transfer manifest itself lives in the shared ``fileflow_contract``
package (``Manifest``, ``ManifestFile``, ``MANIFEST_VERSION``), not
here: it is the contract with the other side, and other projects
need it too.
"""

from __future__ import annotations

from dataclasses import dataclass

STATES = (
    "RECEIVED",
    "VERIFIED",
    "PROCESSING",
    "PROCESSED",
    "PUBLISHED",
    "UPLOADED",
    "FAILED",
    "QUARANTINED",
    "DUPLICATE",
    # reverse flow (SMB -> FTP outgoing -> website)
    "OUTBOUND_READY",
    "UPLOADED_TO_WEBSITE",
)


@dataclass
class RunRecord:
    """One ledger row: a run's identity, state, and recorded detail."""

    run_id: str
    job: str
    idempotency_key: str
    state: str
    detail: dict
    created_at: str
    updated_at: str

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "job": self.job,
            "idempotency_key": self.idempotency_key,
            "state": self.state,
            "detail": self.detail,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
