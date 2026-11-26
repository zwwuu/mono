"""Application -- stage-outbound use case (reverse flow).

Watches ``<share_root>/<requests_dir>/<job>/`` for new files.
A file is only picked when it is stable: not a .part, and its
mtime is at least ``min_age_seconds`` old (default 2s), so a
writer still copying the file is never picked up mid-write.
(Drop writers should still write .part + rename when they can.)

One scan stages one *batch* per job: every stable file currently
in the job's requests dir is one job, because the job only
completes when all its files are uploaded to the website together.
Files dropped later form a later batch. For each batch, in order:
  1. seal it: one new run_id, one manifest listing ALL the files
     with SHA-256 + size, one idempotency key;
  2. copy every data file + the manifest to FTP 1
     ``outgoing/<job>/`` via the source transport (.part +
     rename, manifest last -- the same trigger contract), with
     the size of each copy verified as it lands;
  3. only then move the SMB originals to
     ``<share_root>/<archive_dir>/<job>/`` -- the move is what
     stops them being picked twice. The archive stays on the share,
     which only this machine can see.
A file with the same job + name + hash already staged in a
completed batch is a duplicate: it is archived without being
staged a second time. (The check reads the completed batches'
recorded file lists -- a batch is one ledger run, so there is no
per-file row to look up. The lookup itself lives in
``fileflow_contract.tracking``.)
Returns counts, e.g. {"OUTBOUND_READY": 1} for one batch.
"""

from __future__ import annotations

import itertools
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from fileflow_contract import (
    MANIFEST_VERSION,
    Manifest,
    ManifestFile,
    file_already_recorded,
    file_entry,
    idempotency_key,
    new_run_id,
    sha256_file,
    utcnow_iso,
    write_manifest,
)

from ..domain.ports import RunRepository, Transport
from ..infrastructure.config import WatcherConfig
from ..infrastructure.filesystem import PART_SUFFIX, atomic_move, log
from ..infrastructure.retry import with_retries


class StageOutboundUseCase:
    def __init__(
        self,
        cfg: WatcherConfig,
        *,
        repository: RunRepository,
        intake: Transport,
        sleep: Callable[[float], None] | None = None,
    ):
        self.cfg = cfg
        self.repository = repository
        self.intake = intake
        self.share_root = Path(cfg.share_root) if cfg.share_root else Path(".")
        self._sleep = sleep or time.sleep

    def _archive(self, src: Path, archive_dir: Path, job: str) -> Path:
        """Move a staged SMB original into the per-job archive dir."""
        dest = archive_dir / job / src.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        return atomic_move(src, dest)

    def run(self) -> dict[str, int]:
        outbound_cfg = self.cfg.outbound
        if outbound_cfg is None:
            return {}
        requests_dir = self.share_root / outbound_cfg.requests_dir
        archive_dir = self.share_root / outbound_cfg.archive_dir
        min_age = outbound_cfg.min_age_seconds
        now = time.time()
        counts: dict[str, int] = {}
        for job_dir in sorted(requests_dir.iterdir()) if requests_dir.is_dir() else []:
            job = job_dir.name
            if job not in self.cfg.jobs:
                continue
            stable: list[Path] = []
            for src in sorted(job_dir.iterdir()):
                if (
                    not src.is_file()
                    or src.suffix == PART_SUFFIX
                    or src.name.endswith(".manifest.json")
                ):
                    continue
                if now - src.stat().st_mtime < min_age:
                    continue  # still being written; try next scan
                if src.stat().st_size == 0:
                    log.warning("outbound %s: empty file, skipped", src.name)
                    continue
                stable.append(src)
            if not stable:
                continue
            # Split off files already staged in an earlier batch.
            # Only completed batches count: a file from a failed batch
            # stages again on the next scan. Read the completed runs
            # once -- the list is re-checked for every file in the batch.
            completed = list(
                itertools.chain(
                    self.repository.list_by_state("OUTBOUND_READY"),
                    self.repository.list_by_state("UPLOADED_TO_WEBSITE"),
                )
            )
            fresh: list[tuple[Path, str]] = []
            for src in stable:
                input_sha = sha256_file(src)
                if file_already_recorded(completed, job, src.name, input_sha):
                    self._archive(src, archive_dir, job)
                    counts["DUPLICATE"] = counts.get("DUPLICATE", 0) + 1
                    log.warning("outbound %s duplicate, archived without re-staging", src.name)
                else:
                    fresh.append((src, input_sha))
            if not fresh:
                continue
            # One run, one manifest, one idempotency key for the batch:
            # job|run_id. The run_id is the batch's identity; no content
            # hash is needed, and none could name the whole set.
            run_id = new_run_id()
            key = idempotency_key(job, run_id)
            if not self.repository.claim(run_id=run_id, job=job, idempotency_key=key):
                # Practically unreachable: the key carries the random
                # run_id, so only a run_id collision lands here. Leave
                # the files in place; the next scan stages them.
                log.error("outbound %s: could not claim batch run %s", job, run_id)
                continue
            manifest = Manifest(
                version=MANIFEST_VERSION,
                run_id=run_id,
                job=job,
                idempotency_key=key,
                files=[ManifestFile(**file_entry(src, "output")) for src, _ in fresh],
                direction="outbound",
                created_at=utcnow_iso(),
                extra={"source": (f"smb:{outbound_cfg.requests_dir}/{job}/")},
            )
            out_remote = f"outgoing/{job}"
            # Copy every data file first, manifest last, both via .part +
            # rename. The transport verifies each copy (size, then
            # SHA-256 download-back) as it lands, so no second
            # verification pass is needed. A transient failure is
            # retried; a hash mismatch fails the batch immediately.
            try:
                self.intake.makedirs(out_remote)
                with tempfile.TemporaryDirectory() as tmp:
                    manifest_path = Path(tmp) / f"{run_id}.manifest.json"
                    write_manifest(manifest_path, manifest)
                    with_retries(
                        lambda: self.intake.upload_many_with_manifest(
                            [(src, src.name) for src, _ in fresh],
                            manifest_path,
                            out_remote,
                        ),
                        sleep=self._sleep,
                    )
            except (OSError, ValueError) as e:
                self.repository.set_state(run_id, "FAILED", reason=str(e))
                counts["FAILED"] = counts.get("FAILED", 0) + 1
                log.error("outbound batch %s failed staging: %s", run_id, e)
                continue
            self.repository.set_state(
                run_id,
                "OUTBOUND_READY",
                outgoing=out_remote,
                files=[{"name": src.name, "sha256": sha} for src, sha in fresh],
            )
            for src, _ in fresh:
                self._archive(src, archive_dir, job)
            counts["OUTBOUND_READY"] = counts.get("OUTBOUND_READY", 0) + 1
            log.info(
                "outbound batch %s staged %d file(s) in %s (run %s)",
                job,
                len(fresh),
                out_remote,
                run_id,
            )
        return counts
