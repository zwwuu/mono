"""Application -- pickup use case (reverse flow).

The FTP server's watcher stages files from the SMB share into
``outgoing/<job>/`` -- one batch per job, one manifest listing all
of the batch's files. This use case picks a batch up and uploads
it to the website in a single POST carrying all the files: verify
every manifest hash before uploading, check the website's
response (2xx), and only then delete the outgoing files on the
FTP server. A batch is all or nothing: if any file is missing,
corrupt, or the upload fails, nothing is uploaded and the whole
batch stays in ``outgoing/`` for the next pass.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..domain.entities import Manifest
from ..domain.ports import RunRepository, Transport, WebsiteUploader
from ..infrastructure.filesystem import log, read_manifest, verify_files


class PickupUseCase:
    def __init__(
        self,
        cfg: dict,
        *,
        transport: Transport,
        repository: RunRepository,
        uploader: WebsiteUploader,
    ):
        self.cfg = cfg
        self.transport = transport
        self.repository = repository
        self.uploader = uploader

    def run(self) -> dict[str, int]:
        """Pick up staged batches from FTP outgoing/<job>/ and upload
        them to the website. Returns counts, e.g.
        {"UPLOADED_TO_WEBSITE": 1} for one batch."""
        pickup_cfg = self.cfg.get("pickup")
        if not pickup_cfg:
            return {}
        jobs = pickup_cfg.get("jobs") or [self.cfg["job"]]
        counts: dict[str, int] = {}
        for job in jobs:
            remote_dir = f"outgoing/{job}"
            for manifest_name in sorted(self.transport.list_names(remote_dir)):
                if not manifest_name.endswith(".manifest.json"):
                    continue
                staging = Path(self.cfg["staging_dir"]) / "pickup"
                staging.mkdir(parents=True, exist_ok=True)
                local_manifest = staging / manifest_name
                try:
                    self.transport.download(remote_dir, manifest_name, local_manifest)
                    manifest = Manifest.from_dict(read_manifest(local_manifest))
                except (ValueError, OSError, json.JSONDecodeError) as e:
                    log.error("pickup: bad manifest %s: %s", manifest_name, e)
                    counts["FAILED"] = counts.get("FAILED", 0) + 1
                    continue
                if not manifest.files:
                    log.error("pickup: manifest %s lists no files", manifest_name)
                    counts["FAILED"] = counts.get("FAILED", 0) + 1
                    continue
                run_id = manifest.run_id
                remote_names = set(self.transport.list_names(remote_dir))
                if any(f.name not in remote_names for f in manifest.files):
                    # Batch incomplete: a file is still arriving. Leave
                    # the whole batch alone; try again next pass.
                    log.info("pickup: batch %s incomplete, waiting", run_id)
                    continue
                local_files: list[Path] = []
                failed = False
                for entry in manifest.files:
                    local_file = staging / entry.name
                    try:
                        self.transport.download(remote_dir, entry.name, local_file)
                    except OSError as e:
                        log.error("pickup: download of %s failed: %s", entry.name, e)
                        counts["FAILED"] = counts.get("FAILED", 0) + 1
                        failed = True
                        break
                    local_files.append(local_file)
                if failed:
                    continue
                problems = verify_files(staging, manifest)
                if problems:
                    # Hash mismatch: leave the outgoing files alone for
                    # the operator; never upload a corrupt file.
                    log.error("pickup: batch %s failed verification: %s", run_id, problems)
                    counts["FAILED"] = counts.get("FAILED", 0) + 1
                    continue
                is_new = self.repository.claim(
                    run_id=run_id,
                    job=job,
                    idempotency_key=manifest.idempotency_key,
                )
                record = self.repository.get(run_id)
                if not is_new and record is not None and record.state == "UPLOADED_TO_WEBSITE":
                    # Crash between website upload and outgoing cleanup:
                    # the site already has it, so just clear outgoing.
                    for entry in manifest.files:
                        self.transport.delete(remote_dir, entry.name)
                    self.transport.delete(remote_dir, manifest_name)
                    counts["DUPLICATE"] = counts.get("DUPLICATE", 0) + 1
                    log.warning("pickup: batch %s already uploaded; cleared outgoing", run_id)
                    continue
                try:
                    self.uploader.upload_many(local_files, run_id=run_id, job=job)
                except OSError as e:
                    log.error("pickup: website upload failed for batch %s: %s", run_id, e)
                    counts["FAILED"] = counts.get("FAILED", 0) + 1
                    continue
                # Only now, after a verified upload, clear outgoing.
                for entry in manifest.files:
                    self.transport.delete(remote_dir, entry.name)
                self.transport.delete(remote_dir, manifest_name)
                self.repository.set_state(run_id, "UPLOADED_TO_WEBSITE")
                for local_file in local_files:
                    local_file.unlink(missing_ok=True)
                local_manifest.unlink(missing_ok=True)
                counts["UPLOADED_TO_WEBSITE"] = counts.get("UPLOADED_TO_WEBSITE", 0) + 1
        return counts
