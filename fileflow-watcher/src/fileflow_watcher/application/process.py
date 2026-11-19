"""Application -- process-incoming use case (forward flow).

For each manifest found in incoming/<job>/:
  1. CLAIM   -- atomic move of the data files + manifest from
                incoming/<job>/ to processing/<run_id>/ . A second
                watcher / a restart can never claim the same files
                twice, because the rename is atomic and the source
                is gone.
  2. VERIFY  -- re-compute SHA-256 + size against the manifest.
                Mismatch -> quarantine/<run_id>/, ledger QUARANTINED,
                alert. Nothing is processed, nothing is published.
  3. DEDUP   -- ledger.claim(idempotency_key). Already seen ->
                duplicate/ , ledger DUPLICATE, no re-run, no re-publish.
  4. ROUTE   -- job comes from the manifest / incoming directory.
                Unknown job, or a filename that matches no rule ->
                failed/<run_id>/ + reason.txt. Never guess a procedure.
  5. PROCESS -- run the job's registered procedure on EACH file in
                the manifest, in the workdir. A procedure writes a
                NEW output file; the input is never edited. One
                manifest is one batch: every file is processed, and
                the run only completes when all of them are done.
  6. VERIFY OUTPUT -- each output exists, non-empty, parses if CSV.
  7. PUBLISH -- copy every output + the final manifest to the share
                drive via .part + rename, then READ BACK each share
                copy and compare SHA-256 + size. Only then is the run
                PUBLISHED.
  8. ARCHIVE -- the verified inputs + manifest move to done/<run_id>/.

Failure handling: a procedure/publish exception -> failed/<run_id>/
with reason.txt, ledger FAILED, inputs kept. A later re-upload with
the same run_id + input hash is a duplicate, not a retry; a genuine
retry is a NEW run_id from the agent (new idempotency key).
"""

from __future__ import annotations

from pathlib import Path

from fileflow_contract import (
    Manifest,
    ManifestFile,
    file_entry,
    read_manifest,
    sha256_file,
    utcnow_iso,
    verify_files,
    write_manifest,
)

from ..domain.ports import RunRepository
from ..domain.procedures import get_procedure
from ..infrastructure.filesystem import (
    atomic_move,
    atomic_publish,
    log,
    move_tree,
    read_csv_rows,
)


class ProcessIncomingUseCase:
    def __init__(self, cfg: dict, *, repository: RunRepository, procedure_lookup=get_procedure):
        self.cfg = cfg
        self.repository = repository
        self.procedure_lookup = procedure_lookup
        self.ftp_root = Path(cfg["ftp_root"])
        self.share_root = Path(cfg["share_root"])

    # -- dead-letter helpers -------------------------------------------
    def _quarantine(self, run_dir: Path, run_id: str, reason: str) -> None:
        dest = self.ftp_root / "quarantine" / run_id
        move_tree(run_dir, dest)
        (dest / "reason.txt").write_text(reason, encoding="utf-8")
        self.repository.set_state(run_id, "QUARANTINED", reason=reason)
        log.error("run %s QUARANTINED: %s", run_id, reason)

    def _fail(self, run_dir: Path, run_id: str, reason: str) -> None:
        dest = self.ftp_root / "failed" / run_id
        move_tree(run_dir, dest)
        (dest / "reason.txt").write_text(reason, encoding="utf-8")
        self.repository.set_state(run_id, "FAILED", reason=reason)
        log.error("run %s FAILED: %s", run_id, reason)

    # -- the use case ----------------------------------------------------
    def process(self, manifest_path: Path) -> str:
        """Process one manifest found in incoming/<job>/. Returns final state."""
        manifest = Manifest.from_dict(read_manifest(manifest_path))
        run_id = manifest.run_id
        job = manifest.job
        data_names = [f.name for f in manifest.files]
        job_cfg = self.cfg["jobs"].get(job)
        if job_cfg is None:
            # Unknown job: do not guess. Claim it so it is not re-scanned,
            # then dead-letter it with the reason attached.
            run_dir = self.ftp_root / "processing" / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            for name in [*data_names, manifest_path.name]:
                if name and (manifest_path.parent / name).exists():
                    atomic_move(manifest_path.parent / name, run_dir / name)
            self.repository.claim(
                run_id=run_id,
                job=job,
                idempotency_key=manifest.idempotency_key,
            )
            self._fail(run_dir, run_id, f"unknown job {job!r}")
            return "FAILED"

        # 1. CLAIM -- atomic move into processing/<run_id>/
        run_dir = self.ftp_root / "processing" / run_id
        if run_dir.exists():
            return "RECEIVED"  # already claimed by another pass; leave it
        run_dir.mkdir(parents=True, exist_ok=True)
        for name in [*data_names, manifest_path.name]:
            src = manifest_path.parent / name
            if src.exists():
                atomic_move(src, run_dir / name)
        is_new = self.repository.claim(
            run_id=run_id,
            job=job,
            idempotency_key=manifest.idempotency_key,
        )

        # 2. VERIFY -- manifest vs the bytes that actually arrived
        problems = verify_files(run_dir, manifest)
        if problems:
            self._quarantine(run_dir, run_id, "; ".join(problems))
            return "QUARANTINED"

        # 3. DEDUP -- after verification, but BEFORE any set_state, or a
        #    re-upload would overwrite the PUBLISHED state with VERIFIED
        #    and re-publish. The idempotency key embeds run_id, so a
        #    duplicate is the same run_id re-appearing (re-upload / crash
        #    retry) after publishing.
        record = self.repository.get(run_id)
        if not is_new and record is not None and record.state == "PUBLISHED":
            dest = self.ftp_root / "duplicate" / run_id
            move_tree(run_dir, dest)
            # Do NOT change the ledger state: it must stay PUBLISHED, so
            # reconcile still checks the share copy, and a third re-upload
            # is still recognised as a duplicate.
            log.warning("run %s duplicate, already published; skipped", run_id)
            return "DUPLICATE"
        self.repository.set_state(run_id, "VERIFIED")
        log.info("run %s verified %d file(s)", run_id, len(data_names))

        # 4. PROCESS -- every file in the batch, one procedure each
        self.repository.set_state(run_id, "PROCESSING")
        workdir = run_dir / "work"
        workdir.mkdir(exist_ok=True)
        proc = self.procedure_lookup(job_cfg["procedure"])
        outputs: list[Path] = []
        try:
            for data_name in data_names:
                output = proc["fn"](run_dir / data_name, workdir, {"job": job, "run_id": run_id})
                if not output.exists() or output.stat().st_size == 0:
                    raise ValueError(f"procedure produced no output: {output.name}")
                if job_cfg.get("expect_csv"):
                    read_csv_rows(output)  # raises ValueError when unparseable
                outputs.append(output)
        except Exception as e:  # noqa: BLE001 -- a procedure failure is a dead-letter
            self._fail(run_dir, run_id, f"procedure {job_cfg['procedure']}: {e}")
            return "FAILED"
        self.repository.set_state(run_id, "PROCESSED")
        log.info(
            "run %s processed by %s -> %d file(s)",
            run_id,
            job_cfg["procedure"],
            len(outputs),
        )

        # 5. PUBLISH to the share drive (only this machine can see it)
        #    .part + rename, then read-back verification, per file.
        share_dir = self.share_root / job_cfg.get("share_dir", job)
        final_manifest = Manifest(
            version=manifest.version,
            run_id=run_id,
            job=job,
            idempotency_key=manifest.idempotency_key,
            files=[ManifestFile(**file_entry(output, "output")) for output in outputs],
            created_at=utcnow_iso(),
            extra={
                "source_run": manifest.to_dict(),
                "procedure": job_cfg["procedure"],
                "procedure_version": proc["version"],
            },
        )
        published: list[Path] = []
        try:
            for output, entry in zip(outputs, final_manifest.files, strict=True):
                out_name = job_cfg.get("output_pattern", "{name}").format(
                    name=output.name, run_id=run_id, job=job
                )
                published_path = atomic_publish(output, share_dir / out_name)
                # read-back: the share copy must hash exactly like the output
                if sha256_file(published_path) != entry.sha256:
                    raise OSError(f"read-back mismatch on share for {out_name}")
                published.append(published_path)
            manifest_name = f"{run_id}.manifest.json"
            write_manifest(share_dir / manifest_name, final_manifest)
        except Exception as e:  # noqa: BLE001 -- a publish failure is a dead-letter
            self._fail(run_dir, run_id, f"publish to share: {e}")
            return "FAILED"

        # 6. ARCHIVE the verified inputs on the FTP server
        self.repository.set_state(run_id, "PUBLISHED", published=[str(p) for p in published])
        done = self.ftp_root / "done" / run_id
        move_tree(run_dir, done)
        log.info("run %s PUBLISHED %d file(s) to %s", run_id, len(published), share_dir)
        return "PUBLISHED"
