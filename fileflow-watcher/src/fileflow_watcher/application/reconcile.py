"""Application -- reconcile use case (daily check).

Every PUBLISHED run must still have its file on the share.
Returns a list of problems (empty = share matches the ledger).
"""

from __future__ import annotations

from pathlib import Path

from ..domain.ports import RunRepository


class ReconcileUseCase:
    def __init__(self, cfg: dict, *, repository: RunRepository):
        self.cfg = cfg
        self.repository = repository

    def run(self) -> list[str]:
        problems: list[str] = []
        for run in self.repository.list_by_state("PUBLISHED"):
            record = self.repository.get(run.run_id)
            published = record.detail.get("published") if record else None
            # A batch publishes several files; older rows recorded one.
            paths = published if isinstance(published, list) else [published]
            for path in paths:
                if not path or not Path(path).exists():
                    problems.append(f"{run.run_id}: published file missing: {path}")
        return problems
