"""Application -- dry-run use case: simulate a full run.

The pull leg executes for real into a temporary staging area
(``<staging>/dry-run-<run_id>``) so content checks run -- download,
validate, rename, seal the manifest -- and the push leg then scans
the real staging dir read-only and reports what would upload
(including the just-pulled temporary batch). Then the temporary
staging is discarded.

A dry run changes nothing: no uploads, no ledger writes, no
deletes of real staging, no emails. It exits 0 unless the pull
leg itself fails (a download or validation failure), in which
case it raises like a failed run -- the temporary staging is
discarded either way.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from pathlib import Path

from ..domain.integrity import Context
from ..domain.ports import RunRepository, Source, Transport
from ..infrastructure.config import AgentConfig
from ..infrastructure.filesystem import log
from .pull import PullUseCase
from .push import PushUseCase


class DryRunUseCase:
    def __init__(
        self,
        cfg: AgentConfig,
        *,
        source: Source,
        transport: Transport,
        repository: RunRepository,
        sleep: Callable[[float], None] | None = None,
    ):
        self.cfg = cfg
        self.source = source
        self.transport = transport
        self.repository = repository
        self._sleep = sleep or time.sleep

    def _discard_temp_staging(self) -> None:
        """Remove every ``dry-run-*`` folder: simulations only, the
        push stage never picks them up, so deleting them loses no
        real batch."""
        staging_root = Path(Context(self.cfg.variables).local_path(self.cfg.staging_dir))
        if not staging_root.is_dir():
            return
        for sub in staging_root.iterdir():
            if sub.is_dir() and sub.name.startswith("dry-run-"):
                shutil.rmtree(sub, ignore_errors=True)

    def run(self) -> dict:
        try:
            pulled = PullUseCase(
                self.cfg,
                source=self.source,
                repository=self.repository,
                notifier=None,
                sleep=self._sleep,
                dry_run=True,
            ).run()
        except Exception:
            # A failed pull keeps its staging for inspection -- but
            # a dry run leaves nothing behind, so discard it.
            self._discard_temp_staging()
            raise
        temp = Path(pulled["staging"])
        try:
            pushed = PushUseCase(
                self.cfg,
                transport=self.transport,
                repository=self.repository,
                notifier=None,
                sleep=self._sleep,
                dry_run=True,
                # The just-pulled temporary batch is the one batch
                # the push leg must report; any other dry-run-*
                # folder (a crashed dry run's leftover) stays out.
                include_dirs=[temp],
            ).run()
        finally:
            # The temporary batch is reported, then discarded --
            # a dry run leaves nothing behind.
            shutil.rmtree(temp, ignore_errors=True)
        log.info(
            "dry run: pull would stage %d file(s); push would upload %d file(s)",
            len(pulled["outputs"]),
            pushed["files"],
        )
        return {
            "status": "DRY_RUN",
            "pulled": pulled,
            "would_upload": pushed["would_upload"],
            "would_fail": pushed["would_fail"],
            "destination": pushed["destination"],
        }
