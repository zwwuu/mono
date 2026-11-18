"""Application -- scan use case: one polling pass.

Walks incoming/<job>/ for manifests, processes each complete one
(data file landed -- a lone manifest means the agent's upload is
still in flight, since it uploads data first and the manifest last),
then runs the reverse flow (share upload-requests -> outgoing/).
Returns the counts of final states, e.g. {"PUBLISHED": 1}.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..domain.entities import Manifest
from ..domain.ports import RunRepository
from ..infrastructure.filesystem import log, read_manifest
from .outbound import StageOutboundUseCase
from .process import ProcessIncomingUseCase


class ScanUseCase:
    def __init__(
        self,
        cfg: dict,
        *,
        repository: RunRepository,
        incoming: ProcessIncomingUseCase,
        outbound: StageOutboundUseCase,
    ):
        self.cfg = cfg
        self.repository = repository
        self.incoming = incoming
        self.outbound = outbound
        self.ftp_root = Path(cfg["ftp_root"])

    def run(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        incoming = self.ftp_root / "incoming"
        for manifest_path in sorted(incoming.glob("*/*.manifest.json")):
            # Ignore a manifest whose data file has not landed yet:
            # the agent uploads data first, manifest last, so a lone
            # manifest means the upload is still in flight.
            try:
                manifest = Manifest.from_dict(read_manifest(manifest_path))
                data_name = manifest.output_file().name
            except (KeyError, ValueError, OSError, json.JSONDecodeError) as _:
                continue
            if not (manifest_path.parent / data_name).exists():
                continue
            try:
                state = self.incoming.process(manifest_path)
            except Exception as e:  # noqa: BLE001 -- one bad manifest must not kill the scan
                log.error("run at %s failed unexpectedly: %s", manifest_path, e)
                state = "FAILED"
            counts[state] = counts.get(state, 0) + 1
        # Reverse flow: SMB upload-requests -> FTP outgoing.
        for state, n in self.outbound.run().items():
            counts[state] = counts.get(state, 0) + n
        return counts
