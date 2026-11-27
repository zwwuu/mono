"""Application -- push use case (second stage of the forward flow).

Uploads batches the pull stage left in staging. One scan pass:
  1. scan ``<staging_dir>/`` for subdirs holding a manifest
     (``<run_id>.manifest.json``), oldest first. A subdir with no
     manifest is skipped -- a pull still in flight, or a failed
     pull whose manifest was never sealed. A ``dry-run-*`` folder
     is a simulation, never a real batch, and is skipped too;
  2. per batch: read the manifest and re-verify every listed
     output against it (the files sat in staging; they may have
     been touched). A missing or corrupt file fails the batch --
     it stays staged for the next push, the other batches still go;
  3. upload every output, then the manifest *last*, via .part +
     rename, to the destination directory (``destination.directory``
     when the config names one, else ``incoming/<job>/``), hash-
     verified exactly as a full run does;
  4. record the run UPLOADED on the same run_id the pull claimed
     (creating the row when the ledger has no row for it -- a lost
     or reset ledger; the manifest is the source of truth for what
     is staged), then delete the staging folder.

A failed batch stays staged and its run row stays STAGED -- it
must not be marked FAILED, or the pull's skip check would treat
its files as fresh and pull them again under a new run_id. The
next push retries the folder. The failure is logged, emailed in
the run's one summary email, and the run exits 1 with each failed
batch named; the other batches are unaffected.

In dry-run mode the push is read-only: it scans, verifies, and
reports what would upload, but uploads nothing, writes nothing
to the ledger, deletes nothing, and sends no email.

The use case only talks to ports (Transport, RunRepository). It
never opens a socket or a database itself.
"""

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Callable
from pathlib import Path

from fileflow_contract import Manifest, read_manifest, verify_files

from ..domain.integrity import Context
from ..domain.ports import Notifier, RunRepository, Transport
from ..infrastructure.config import AgentConfig
from ..infrastructure.filesystem import log
from ..infrastructure.retry import with_retries


def _age_text(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m"
    return f"{seconds / 3600:.1f}h"


class PushUseCase:
    def __init__(
        self,
        cfg: AgentConfig,
        *,
        transport: Transport,
        repository: RunRepository,
        notifier: Notifier | None = None,
        sleep: Callable[[float], None] | None = None,
        dry_run: bool = False,
        include_dirs: list[str | Path] | None = None,
    ):
        self.cfg = cfg
        self.transport = transport
        self.repository = repository
        self.notifier = notifier
        self._sleep = sleep or time.sleep
        self.dry_run = dry_run
        # Batch dirs to consider even when they look like simulations
        # (a dry run's own temporary batch, passed by the caller).
        self._include_dirs = {Path(d).name for d in (include_dirs or [])}

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
        if self.notifier is not None and not self.dry_run:
            self.notifier.notify(
                job=self.cfg.job,
                run_id=run_id,
                status=status,
                files=files,
                destination=destination,
                error=error,
                summary=summary,
            )

    def _remote_dir(self, ctx: Context) -> str:
        dir_template = self.cfg.destination.directory
        if dir_template:
            return ctx.remote_path(dir_template)
        return f"incoming/{self.cfg.job}"

    def _staged_batches(self, staging_root: Path) -> list[Path]:
        """Subdirs holding a manifest, oldest first. No-manifest
        folders are a pull still in flight or a failed pull;
        ``dry-run-*`` folders are simulations, never real batches."""
        if not staging_root.is_dir():
            return []
        batches = []
        for sub in sorted(staging_root.iterdir()):
            if not sub.is_dir():
                continue
            if sub.name.startswith("dry-run-") and sub.name not in self._include_dirs:
                log.debug("push: skipping simulation folder %s", sub.name)
                continue
            if not list(sub.glob("*.manifest.json")):
                log.debug("push: skipping %s, no manifest", sub.name)
                continue
            batches.append(sub)
        batches.sort(key=lambda d: d.stat().st_mtime)
        return batches

    def run(self) -> dict:
        cfg = self.cfg
        job = cfg.job
        started = time.monotonic()
        ctx = Context(cfg.variables)
        staging_root = Path(ctx.local_path(cfg.staging_dir))
        remote_dir = self._remote_dir(ctx)
        uploaded: list[tuple[str, list[str]]] = []
        failures: list[tuple[str, str]] = []
        uploaded_bytes = 0
        # Dry-run report: what would go up, and what would fail.
        would_upload: list[tuple[str, list[str], int]] = []
        would_fail: list[tuple[str, str]] = []
        for batch_dir in self._staged_batches(staging_root):
            manifest_name = sorted(p.name for p in batch_dir.glob("*.manifest.json"))[0]
            manifest_path = batch_dir / manifest_name
            try:
                manifest = Manifest.from_dict(read_manifest(manifest_path))
            except (ValueError, OSError, json.JSONDecodeError) as e:
                reason = f"bad manifest {manifest_name}: {e}"
                log.error("push: batch %s failed: %s", batch_dir.name, reason)
                (would_fail if self.dry_run else failures).append((batch_dir.name, reason))
                continue
            run_id = manifest.run_id
            if manifest.job != job:
                # Another job's batch in this staging dir (two jobs
                # sharing one staging_dir): not this run's business.
                log.warning(
                    "push: skipping batch %s, manifest job %r is not this job %r",
                    run_id,
                    manifest.job,
                    job,
                )
                continue
            if not manifest.files:
                reason = f"manifest {manifest_name} lists no files"
                log.error("push: batch %s failed: %s", run_id, reason)
                (would_fail if self.dry_run else failures).append((run_id, reason))
                continue
            problems = verify_files(batch_dir, manifest)
            if problems:
                # Hash/size mismatch: leave the batch staged for the
                # operator; never upload a corrupt file.
                reason = f"verification failed: {problems}"
                log.error("push: batch %s failed: %s", run_id, reason)
                (would_fail if self.dry_run else failures).append((run_id, reason))
                continue
            names = [entry.name for entry in manifest.files]
            total_bytes = sum(entry.size for entry in manifest.files)
            if self.dry_run:
                log.info(
                    "push: batch %s would upload %d file(s) to %s", run_id, len(names), remote_dir
                )
                would_upload.append((run_id, names, total_bytes))
                continue
            outputs = [(batch_dir / entry.name, entry.name) for entry in manifest.files]
            batch_started = time.monotonic()
            try:
                # A transient upload failure is retried (OSError
                # only); a hash mismatch is a ValueError and fails
                # immediately. Either way the batch stays staged
                # and its run row stays STAGED for the next push.
                with_retries(
                    lambda: self.transport.upload_many_with_manifest(
                        outputs, manifest_path, remote_dir
                    ),
                    sleep=self._sleep,
                )
            except (OSError, ValueError) as e:
                reason = f"upload failed: {e}"
                log.error("push: batch %s failed: %s", run_id, reason)
                failures.append((run_id, reason))
                continue
            # The pull claimed this run already; claim again only to
            # recreate the row when the ledger lost it (the manifest
            # is the source of truth for what is staged). set_state
            # merges detail, so the pulled files list recorded by
            # the pull survives the move to UPLOADED.
            self.repository.claim(
                run_id=run_id,
                job=job,
                idempotency_key=manifest.idempotency_key,
            )
            self.repository.set_state(run_id, "UPLOADED", outputs=names)
            shutil.rmtree(batch_dir)
            uploaded_bytes += total_bytes
            uploaded.append((run_id, names))
            summary = (
                f"run {run_id} job={job} UPLOADED {len(names)} file(s), "
                f"{total_bytes} bytes in {time.monotonic() - batch_started:.1f}s -> {remote_dir}"
            )
            log.info(summary)
        total_files = sum(len(names) for _, names in uploaded)
        if self.dry_run:
            return {
                "status": "DRY_RUN",
                "would_upload": would_upload,
                "would_fail": would_fail,
                "files": sum(len(names) for _, names, _ in would_upload),
                "destination": remote_dir,
            }
        if not uploaded and not failures:
            log.info("push: nothing staged, nothing to do")
            return {"status": "NOOP", "uploaded": [], "files": 0}
        all_ids = ", ".join(dict.fromkeys([rid for rid, _ in uploaded + failures]))
        status = "FAILED" if failures else "UPLOADED"
        summary = (
            f"run {all_ids} job={job} {status} {total_files} file(s), "
            f"{uploaded_bytes} bytes in {time.monotonic() - started:.1f}s -> {remote_dir}"
        )
        if len(uploaded) + len(failures) > 1 or failures:
            # A single uploaded batch already logged its own summary
            # line above; the aggregate only adds information when
            # several batches ran, or when a batch failed (a failed
            # batch logs no per-batch summary).
            log.info(summary)
        self._notify(
            run_id=all_ids,
            status=status,
            files=[name for _, names in uploaded for name in names],
            destination=remote_dir,
            error="; ".join(f"{rid}: {reason}" for rid, reason in failures) or None,
            summary=summary,
        )
        if not self.dry_run:
            leftovers = self._staged_batches(staging_root)
            if leftovers:
                oldest = time.time() - min(d.stat().st_mtime for d in leftovers)
                log.info(
                    "push: %d staged batch(es) left behind, oldest %s old",
                    len(leftovers),
                    _age_text(oldest),
                )
        if failures:
            raise RuntimeError(
                "push failed for " + ", ".join(f"{rid} ({reason})" for rid, reason in failures)
            )
        return {
            "status": "UPLOADED",
            "uploaded": [rid for rid, _ in uploaded],
            "files": total_files,
        }
