"""Application -- validate use case: check the manifest contract in staging.

Scans ``<staging_dir>/`` for batch folders and verifies each batch's
manifest contract: the manifest parses as JSON and as a contract
``Manifest`` (structure + version), its run_id matches the staging
folder name, and every listed file exists in the folder with
matching SHA-256 and size (the contract's ``verify_files`` -- never
reimplemented here).

A folder without a manifest is an incomplete batch: a batch that
can never push means staging is not healthy, so it fails the run.
``dry-run-*`` folders are simulations, never real batches. A batch
for another job (two jobs sharing one staging_dir) is not this
run's business, same as push.

No ledger reads, no transfers, no writes, no emails. Exit 0 when
every batch is valid (or nothing is staged), 1 otherwise.
"""

from __future__ import annotations

import json
from pathlib import Path

from fileflow_contract import (
    MANIFEST_VERSION,
    Manifest,
    read_manifest,
    verify_files,
)

from ..domain.integrity import Context
from ..infrastructure.config import AgentConfig


class ValidateUseCase:
    def __init__(self, cfg: AgentConfig):
        self.cfg = cfg

    def _verify_batch(self, batch_dir: Path) -> dict:
        """Verify one batch folder; returns its report dict."""
        manifest_name = sorted(p.name for p in batch_dir.glob("*.manifest.json"))[0]
        manifest_path = batch_dir / manifest_name
        try:
            raw = read_manifest(manifest_path)
        except (OSError, json.JSONDecodeError) as e:
            return {
                "run_id": batch_dir.name,
                "status": "FAILED",
                "problems": [f"malformed manifest: {e}"],
            }
        try:
            manifest = Manifest.from_dict(raw)
        except (KeyError, TypeError, AttributeError) as e:
            return {
                "run_id": batch_dir.name,
                "status": "FAILED",
                "problems": [f"malformed manifest: {e}"],
            }
        if manifest.job != self.cfg.job:
            # Another job's batch in this staging dir: not this
            # run's business (push skips it the same way).
            return {
                "run_id": batch_dir.name,
                "status": "SKIPPED",
                "problems": [f"other job {manifest.job!r}"],
            }
        problems: list[str] = []
        if manifest.version != MANIFEST_VERSION:
            # An unknown version may not even list files the same
            # way; report it and stop here, don't trust the rest.
            problems.append(
                f"version mismatch: manifest version {manifest.version}, "
                f"contract version {MANIFEST_VERSION}"
            )
            return {"run_id": batch_dir.name, "status": "FAILED", "problems": problems}
        if manifest.run_id != batch_dir.name:
            problems.append(
                f"run_id mismatch: manifest {manifest.run_id!r} != folder {batch_dir.name!r}"
            )
        if not manifest.files:
            problems.append("manifest lists no files")
        else:
            problems.extend(verify_files(batch_dir, manifest))
        if problems:
            return {"run_id": batch_dir.name, "status": "FAILED", "problems": problems}
        return {"run_id": batch_dir.name, "status": "OK", "problems": []}

    def run(self) -> dict:
        ctx = Context(self.cfg.variables)
        staging_root = Path(ctx.local_path(self.cfg.staging_dir))
        batches: list[dict] = []
        if staging_root.is_dir():
            for sub in sorted(staging_root.iterdir()):
                if not sub.is_dir():
                    continue
                if sub.name.startswith("dry-run-"):
                    batches.append(
                        {
                            "run_id": sub.name,
                            "status": "SKIPPED",
                            "problems": ["simulation folder"],
                        }
                    )
                    continue
                if not list(sub.glob("*.manifest.json")):
                    batches.append(
                        {
                            "run_id": sub.name,
                            "status": "INCOMPLETE",
                            "problems": ["no manifest"],
                        }
                    )
                    continue
                batches.append(self._verify_batch(sub))
        status = "VALID" if all(b["status"] in ("OK", "SKIPPED") for b in batches) else "INVALID"
        return {"status": status, "batches": batches}
