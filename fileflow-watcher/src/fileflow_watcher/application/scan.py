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

from fileflow_contract import Manifest, read_manifest

from ..domain.ports import RunRepository
from ..infrastructure.filesystem import log
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
            # Ignore a manifest whose data files have not all landed yet:
            # the agent uploads data first, manifest last, so a manifest
            # with a missing file means the upload is still in flight.
            # A manifest with no files at all is malformed; leave it for
            # the operator instead of crashing the scan.
            try:
                manifest = Manifest.from_dict(read_manifest(manifest_path))
            except (KeyError, ValueError, OSError, json.JSONDecodeError) as _:
                continue
            if not manifest.files or any(
                not (manifest_path.parent / f.name).exists() for f in manifest.files
            ):
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
