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
import time
from collections.abc import Callable
from pathlib import Path

from fileflow_contract import Manifest, read_manifest, verify_files

from ..domain.integrity import Context
from ..domain.ports import Notifier, RunRepository, Transport, WebsiteUploader
from ..infrastructure.config import AgentConfig
from ..infrastructure.filesystem import log
from ..infrastructure.retry import with_retries


class PickupUseCase:
    def __init__(
        self,
        cfg: AgentConfig,
        *,
        transport: Transport,
        repository: RunRepository,
        uploader: WebsiteUploader | None,
        notifier: Notifier | None = None,
        sleep: Callable[[float], None] | None = None,
    ):
        self.cfg = cfg
        self.transport = transport
        self.repository = repository
        self.uploader = uploader
        self.notifier = notifier
        self._sleep = sleep or time.sleep

    def _notify(
        self,
        *,
        run_id: str,
        status: str,
        files: list[str],
        destination: str | None = None,
        error: str | None = None,
        summary: str | None = None,
    ) -> None:
        if self.notifier is not None:
            self.notifier.notify(
                job=self.cfg.job,
                run_id=run_id,
                status=status,
                files=files,
                destination=destination,
                error=error,
                summary=summary,
            )

    def run(self) -> dict[str, int]:
        """Pick up staged batches from FTP outgoing/<job>/ and upload
        them to the website. Returns counts, e.g.
        {"UPLOADED_TO_WEBSITE": 1} for one batch."""
        if self.cfg.deliver is None:
            return {}
        job = self.cfg.job
        started = time.monotonic()
        # One Context per pass: the date in staging_dir is this
        # pass's date, not the process's startup date.
        ctx = Context(
            job,
            self.cfg.category,
            self.cfg.variables,
            self.cfg.date_format,
            self.cfg.time_format,
        )
        counts: dict[str, int] = {}
        # One email per run, at the end: the outcomes of every batch
        # this pass handled. A pass that only cleared DUPLICATEs or
        # found nothing sends nothing, like a SKIPPED download run.
        uploaded: list[tuple[str, list[str]]] = []
        failures: list[tuple[str, str]] = []
        uploaded_bytes = 0
        remote_dir = f"outgoing/{job}"
        for manifest_name in sorted(self.transport.list_names(remote_dir)):
            if not manifest_name.endswith(".manifest.json"):
                continue
            staging = Path(ctx.local_path(self.cfg.staging_dir)) / "pickup"
            staging.mkdir(parents=True, exist_ok=True)
            local_manifest = staging / manifest_name
            try:
                self.transport.download(remote_dir, manifest_name, local_manifest)
                manifest = Manifest.from_dict(read_manifest(local_manifest))
            except (ValueError, OSError, json.JSONDecodeError) as e:
                log.error("pickup: bad manifest %s: %s", manifest_name, e)
                counts["FAILED"] = counts.get("FAILED", 0) + 1
                failures.append((manifest_name, str(e)))
                continue
            if not manifest.files:
                log.error("pickup: manifest %s lists no files", manifest_name)
                counts["FAILED"] = counts.get("FAILED", 0) + 1
                failures.append((manifest_name, "manifest lists no files"))
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
                    with_retries(
                        lambda entry=entry, local_file=local_file: self.transport.download(
                            remote_dir, entry.name, local_file
                        ),
                        sleep=self._sleep,
                    )
                except OSError as e:
                    log.error("pickup: download of %s failed: %s", entry.name, e)
                    counts["FAILED"] = counts.get("FAILED", 0) + 1
                    failures.append((run_id, f"download of {entry.name} failed: {e}"))
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
                failures.append((run_id, f"verification failed: {problems}"))
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
                with_retries(
                    lambda: self.uploader.upload_many(local_files, run_id=run_id, job=job),
                    sleep=self._sleep,
                )
            except OSError as e:
                log.error("pickup: website upload failed for batch %s: %s", run_id, e)
                counts["FAILED"] = counts.get("FAILED", 0) + 1
                failures.append((run_id, f"website upload failed: {e}"))
                continue
            # Only now, after a verified upload, clear outgoing.
            for entry in manifest.files:
                self.transport.delete(remote_dir, entry.name)
            self.transport.delete(remote_dir, manifest_name)
            self.repository.set_state(run_id, "UPLOADED_TO_WEBSITE")
            uploaded_bytes += sum(entry.size for entry in manifest.files)
            for local_file in local_files:
                local_file.unlink(missing_ok=True)
            local_manifest.unlink(missing_ok=True)
            counts["UPLOADED_TO_WEBSITE"] = counts.get("UPLOADED_TO_WEBSITE", 0) + 1
            uploaded.append((run_id, [entry.name for entry in manifest.files]))
        if failures or uploaded:
            all_ids = ", ".join(dict.fromkeys([rid for rid, _ in uploaded + failures]))
            total_files = sum(len(names) for _, names in uploaded)
            status = "FAILED" if failures else "UPLOADED_TO_WEBSITE"
            summary = (
                f"run {all_ids} job={job} {status} {total_files} file(s), "
                f"{uploaded_bytes} bytes in {time.monotonic() - started:.1f}s -> "
                f"{self.cfg.deliver.upload_url}"
            )
            log.info(summary)
        if failures:
            self._notify(
                run_id=", ".join(dict.fromkeys([rid for rid, _ in uploaded + failures])),
                status="FAILED",
                files=[name for _, names in uploaded for name in names],
                destination=self.cfg.deliver.upload_url,
                error="; ".join(f"{rid}: {reason}" for rid, reason in failures),
                summary=summary,
            )
        elif uploaded:
            self._notify(
                run_id=", ".join(rid for rid, _ in uploaded),
                status="UPLOADED_TO_WEBSITE",
                files=[name for _, names in uploaded for name in names],
                destination=self.cfg.deliver.upload_url,
                summary=summary,
            )
        return counts
