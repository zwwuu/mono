"""Application -- scan use case: one polling pass.

One pass does all three flows, in order:
  1. handoff -- FTP 1 incoming/<job>/ manifests to FTP 2
     processing/<job>/ for external processing;
  2. completion -- FTP 2 success/<job>/ + failed/<job>/ outcomes
     back to FTP 1 done/<job>/ + failed/<job>/, one email per
     finished batch;
  3. reverse flow -- share upload-requests -> FTP 1 outgoing/.
Returns the counts of final states, e.g. {"SENT_TO_PROCESSING": 1}.
"""

from __future__ import annotations

from ..domain.ports import RunRepository, Transport
from ..infrastructure.config import WatcherConfig
from ..infrastructure.filesystem import log
from .handoff import CompletionUseCase, HandoffUseCase
from .outbound import StageOutboundUseCase
from .stale import StaleAlertUseCase


class ScanUseCase:
    def __init__(
        self,
        cfg: WatcherConfig,
        *,
        repository: RunRepository,
        intake: Transport,
        handoff: HandoffUseCase,
        completion: CompletionUseCase,
        outbound: StageOutboundUseCase,
        stale: StaleAlertUseCase | None = None,
    ):
        self.cfg = cfg
        self.repository = repository
        self.intake = intake
        self.handoff = handoff
        self.completion = completion
        self.outbound = outbound
        self.stale = stale

    def run(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        # Handoff: one manifest per batch in FTP 1 incoming/<job>/.
        # Walk every subdirectory, not just the configured jobs: a
        # manifest for an unknown job is dead-lettered, never ignored.
        incoming_root = self.cfg.intake.incoming_dir
        for job in sorted(self.intake.list_names(incoming_root)):
            incoming_dir = f"{incoming_root}/{job}"
            for name in sorted(self.intake.list_names(incoming_dir)):
                if not name.endswith(".manifest.json"):
                    continue
                try:
                    state = self.handoff.handoff(job, name)
                except Exception as e:  # noqa: BLE001 -- one bad manifest must not kill the scan
                    log.error("handoff of %s/%s failed unexpectedly: %s", job, name, e)
                    state = "FAILED"
                if state:
                    counts[state] = counts.get(state, 0) + 1
        # Completion: FTP 2 outcomes for handed-off batches.
        for state, n in self.completion.complete().items():
            counts[state] = counts.get(state, 0) + n
        # Stuck files: alert once per file, never spam. A dead
        # notifier is logged by the use case, not fatal here.
        if self.stale is not None:
            self.stale.run()
        # Reverse flow: SMB upload-requests -> FTP 1 outgoing/.
        for state, n in self.outbound.run().items():
            counts[state] = counts.get(state, 0) + n
        return counts
