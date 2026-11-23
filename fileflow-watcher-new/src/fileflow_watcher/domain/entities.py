"""Domain entities: the objects the business rules are about.

A *run record* is one row of the ledger: a run's identity, state,
and recorded detail. It is a plain dataclass -- no database, no
I/O -- so the use cases can build and inspect it without touching
SQLite.

The transfer manifest itself lives in the shared ``fileflow_contract``
package (``Manifest``, ``ManifestFile``, ``MANIFEST_VERSION``), not
here: it is the contract with the other side, and other projects
need it too.

Run states, forward flow (FTP 1 -> FTP 2 handoff):
  RECEIVED            claimed, handoff not finished yet
  SENT_TO_PROCESSING  files copied to FTP 2, awaiting the processor
  COMPLETED           every file landed in success/ on FTP 2
  FAILED              a file failed (processor failed/ or hash
                      mismatch), or the batch dead-lettered
  QUARANTINED         hash/size mismatch against the manifest --
                      never copied to FTP 2
  DUPLICATE           re-dropped file already recorded: archived
                      on FTP 1 without copying again
"""

from __future__ import annotations

from dataclasses import dataclass

STATES = (
    "RECEIVED",
    "SENT_TO_PROCESSING",
    "COMPLETED",
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
