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
  2. copy every data file + the manifest to
     ``<ftp_root>/outgoing/<job>/`` via .part + rename, manifest
     last (same trigger contract);
  3. verify the outgoing copies hash exactly like the SMB files;
  4. only then move the SMB originals to
     ``<share_root>/<archive_dir>/<job>/`` -- the move is what
     stops them being picked twice. The archive stays on the share,
     which only this machine can see.
A file with the same job + name + hash already staged in a
completed batch is a duplicate: it is archived without being
staged a second time. (The check reads the completed batches'
recorded file lists -- a batch is one ledger run, so there is no
per-file row to look up.)
Returns counts, e.g. {"OUTBOUND_READY": 1} for one batch.
"""

from __future__ import annotations

import time
from pathlib import Path

from fileflow_contract import (
    MANIFEST_VERSION,
    Manifest,
    ManifestFile,
    file_entry,
    idempotency_key,
    new_run_id,
    sha256_file,
    utcnow_iso,
    verify_files,
    write_manifest,
)

from ..domain.ports import RunRepository
from ..infrastructure.filesystem import PART_SUFFIX, atomic_move, atomic_publish, log


class StageOutboundUseCase:
    def __init__(self, cfg: dict, *, repository: RunRepository):
        self.cfg = cfg
        self.repository = repository
        self.ftp_root = Path(cfg["ftp_root"])
        self.share_root = Path(cfg["share_root"])

    def _file_already_staged(self, job: str, name: str, sha: str) -> bool:
        """True if this exact file (job + name + hash) is already in a
        completed outbound batch. A failed batch is not completed, so
        its files stage again on the next scan."""
        for state in ("OUTBOUND_READY", "UPLOADED_TO_WEBSITE"):
            for record in self.repository.list_by_state(state):
                if record.job != job:
                    continue
                for f in record.detail.get("files", []):
                    if f.get("name") == name and f.get("sha256") == sha:
                        return True
        return False

    def run(self) -> dict[str, int]:
        outbound_cfg = self.cfg.get("outbound")
        if not outbound_cfg:
            return {}
        requests_dir = self.share_root / outbound_cfg.get("requests_dir", "upload-requests")
        archive_dir = self.share_root / outbound_cfg.get("archive_dir", "upload-archive")
        min_age = outbound_cfg.get("min_age_seconds", 2)
        now = time.time()
        counts: dict[str, int] = {}
        for job_dir in sorted(requests_dir.iterdir()) if requests_dir.is_dir() else []:
            job = job_dir.name
            job_cfg = self.cfg["jobs"].get(job)
            if job_cfg is None or not job_cfg.get("outbound"):
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
            fresh: list[tuple[Path, str]] = []
            for src in stable:
                input_sha = sha256_file(src)
                if self._file_already_staged(job, src.name, input_sha):
                    dest = archive_dir / job / src.name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    atomic_move(src, dest)
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
                extra={
                    "source": (f"smb:{outbound_cfg.get('requests_dir', 'upload-requests')}/{job}/")
                },
            )
            out_dir = self.ftp_root / "outgoing" / job
            out_dir.mkdir(parents=True, exist_ok=True)
            # Copy every data file first, manifest last, both via .part + rename.
            for src, _ in fresh:
                atomic_publish(src, out_dir / src.name)
            write_manifest(out_dir / f"{run_id}.manifest.json", manifest)
            # Verify the outgoing copies before touching the SMB originals.
            problems = verify_files(out_dir, manifest)
            if problems:
                self.repository.set_state(run_id, "FAILED", reason="; ".join(problems))
                counts["FAILED"] = counts.get("FAILED", 0) + 1
                log.error("outbound batch %s failed verification: %s", run_id, problems)
                continue
            self.repository.set_state(
                run_id,
                "OUTBOUND_READY",
                outgoing=str(out_dir),
                files=[{"name": src.name, "sha256": sha} for src, sha in fresh],
            )
            for src, _ in fresh:
                dest = archive_dir / job / src.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                atomic_move(src, dest)
            counts["OUTBOUND_READY"] = counts.get("OUTBOUND_READY", 0) + 1
            log.info(
                "outbound batch %s staged %d file(s) in %s (run %s)",
                job,
                len(fresh),
                out_dir,
                run_id,
            )
        return counts
