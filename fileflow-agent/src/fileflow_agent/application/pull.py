"""Application -- pull use case (first stage of the forward flow).

One run, no approval step:
  1. download the source file(s) to ``<staging>/<run_id>`` -- one
     file for a website/local source, every matching file for a
     local-dir source. One run is one batch: all the files go up
     together under a single manifest, and the job only completes
     when all of them are uploaded;
  2. validate them (non-empty; CSV must parse if the job says so);
     files already processed in an earlier completed run (same job
     + name + hash, recorded in the ledger) are skipped -- a
     re-run, a re-drop, or a loop pass over an unchanged drop
     folder does not download them again. A changed file (new hash)
     is fresh and is processed. If nothing is fresh, the run is
     SKIPPED: no manifest, no upload, no new ledger row;
  3. first rename / format each one, using the procedure named in
     the config;
  4. seal them: one manifest with SHA-256 + size of every output
     file, run_id, job, procedure + version. Each file keeps its
     own hash in ``files[]`` -- there is no manifest-level input
     hash, because no single hash can name a set of files. The
     idempotency key is ``job|run_id``: the run_id is the run's
     identity, and it is unique per run without any content hash;
  5. record the run STAGED with the list of files it pulled (job +
     name + hash each), which is what the next run's skip check
     reads -- and leave the batch in staging for the push stage
     to upload. Nothing is uploaded here.

In dry-run mode the pull runs for real into a temporary staging
area (``<staging>/dry-run-<run_id>``) so content checks run, but
no ledger row is written and no email is sent; the caller discards
the temporary staging afterwards.

The use case only talks to ports (Source, RunRepository) and to
the procedure registry. It never opens a socket or a database
itself, so it can be tested with a local transport and a throwaway
SQLite file -- which is exactly what the test suite does.
"""

from __future__ import annotations

import shutil
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

from ..domain.integrity import Context
from ..domain.ports import Notifier, RunRepository, Source
from ..domain.procedures import get_procedure
from ..infrastructure.config import AgentConfig, source_reference
from ..infrastructure.filesystem import log, read_csv_rows
from ..infrastructure.retry import with_retries


def validate_input(path: Path, expect_csv: bool) -> None:
    if path.stat().st_size == 0:
        raise ValueError(f"downloaded file is empty: {path.name}")
    if expect_csv:
        rows = read_csv_rows(path)
        if not any(c.strip() for c in rows[0]):
            raise ValueError(f"not a parseable CSV: {path.name}")


class PullUseCase:
    def __init__(
        self,
        cfg: AgentConfig,
        *,
        source: Source,
        repository: RunRepository,
        procedure_lookup=get_procedure,
        notifier: Notifier | None = None,
        sleep: Callable[[float], None] | None = None,
        dry_run: bool = False,
    ):
        self.cfg = cfg
        self.source = source
        self.repository = repository
        self.procedure_lookup = procedure_lookup
        self.notifier = notifier
        self._sleep = sleep or time.sleep
        self.dry_run = dry_run

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

    def run(self) -> dict:
        cfg = self.cfg
        job = cfg.job
        run_id = new_run_id()
        started = time.monotonic()
        # One Context per run, from the job's variables only. A
        # dry run stages into a clearly-marked folder the push
        # stage never picks up on its own.
        ctx = Context(cfg.variables)
        staging_name = f"dry-run-{run_id}" if self.dry_run else run_id
        staging = Path(ctx.local_path(cfg.staging_dir)) / staging_name
        staging.mkdir(parents=True, exist_ok=True)
        log.info("run %s job=%s starting", run_id, job)
        try:
            # 1-2. download, split off already-processed files, validate
            # only the fresh ones. A changed file has a new hash, so it
            # is fresh even under the same name. A transient download
            # failure is retried (OSError only, never validation).
            downloaded = with_retries(
                lambda: self.source.download(staging, run_id), sleep=self._sleep
            )
            if not downloaded:
                # Nothing to transfer: an empty drop folder, or a
                # remote-files list whose entries are all optional
                # and none was ready. That is "nothing to do", not a
                # failure -- the same SKIPPED the
                # all-already-processed case returns. A source that
                # cannot connect, or a required remote-files entry
                # that is missing, still raises above.
                shutil.rmtree(staging)
                log.info("run %s: nothing to transfer, SKIPPED", run_id)
                return {
                    "run_id": run_id,
                    "job": job,
                    "outputs": [],
                    "skipped": [],
                    "status": "SKIPPED",
                    "staging": str(staging),
                }
            fresh: list[tuple[Path, str]] = []
            skipped: list[str] = []
            # Only STAGED/UPLOADED runs count: a file from a failed
            # run is fresh and retries, and a file already staged
            # (pulled but not yet pushed) must not be pulled again.
            recorded = self.repository.list_by_state("STAGED") + self.repository.list_by_state(
                "UPLOADED"
            )
            for path in downloaded:
                input_sha = sha256_file(path)
                if file_already_recorded(recorded, job, path.name, input_sha):
                    log.info("run %s: %s already processed, skipped", run_id, path.name)
                    skipped.append(path.name)
                    continue
                validate_input(path, cfg.expect_csv)
                fresh.append((path, input_sha))
                log.info(
                    "run %s downloaded %s (%d bytes, sha256 %s...)",
                    run_id,
                    path.name,
                    path.stat().st_size,
                    input_sha[:12],
                )
            if not fresh:
                shutil.rmtree(staging)
                log.info("run %s: nothing fresh, SKIPPED (%d file(s))", run_id, len(skipped))
                return {
                    "run_id": run_id,
                    "job": job,
                    "outputs": [],
                    "skipped": skipped,
                    "status": "SKIPPED",
                    "staging": str(staging),
                }

            # 3. first rename / format each fresh file (agent-side procedure)
            proc_name = cfg.procedure
            proc = self.procedure_lookup(proc_name)
            outputs: list[tuple[Path, str]] = []
            for path, _ in fresh:
                formatted = proc.fn(path, staging, {"job": job, "run_id": run_id})
                stem, ext = (
                    formatted.name.rsplit(".", 1) if "." in formatted.name else (formatted.name, "")
                )
                ext = f".{ext}" if ext else ""
                out_name = ctx.render_name(
                    cfg.rename_pattern,
                    name=stem,
                    ext=ext,
                    run_id=run_id,
                )
                output = staging / out_name
                if formatted != output:
                    formatted.replace(output)
                outputs.append((output, out_name))

            # 4. seal with one manifest: every output's hash, run_id,
            # job, procedure + version. No manifest-level input hash.
            manifest = Manifest(
                version=MANIFEST_VERSION,
                run_id=run_id,
                job=job,
                idempotency_key=idempotency_key(job, run_id),
                files=[ManifestFile(**file_entry(output, "output")) for output, _ in outputs],
                created_at=utcnow_iso(),
                extra={
                    "source_url": source_reference(cfg.source),
                    "procedure": proc_name,
                    "procedure_version": proc.version,
                    # Input hashes are provenance / idempotency only: the
                    # input files themselves are NOT uploaded, so they must
                    # not appear in "files" -- the watcher verifies only the
                    # files listed there, i.e. the transferred outputs.
                },
            )
            manifest_path = staging / f"{run_id}.manifest.json"
            write_manifest(manifest_path, manifest)

            # 5. record the run STAGED with the pulled files: the next
            # run's skip check reads this list. The batch stays in
            # staging for the push stage. A dry run writes nothing.
            total_bytes = sum(output.stat().st_size for output, _ in outputs)
            summary = (
                f"run {run_id} job={job} STAGED {len(outputs)} file(s), "
                f"{total_bytes} bytes in {time.monotonic() - started:.1f}s -> {staging}"
            )
            log.info(summary)
            if not self.dry_run:
                self.repository.claim(
                    run_id=run_id,
                    job=job,
                    idempotency_key=manifest.idempotency_key,
                )
                # Record the pulled files on the run: the next run's skip
                # check reads this list.
                self.repository.set_state(
                    run_id,
                    "STAGED",
                    files=[{"name": path.name, "sha256": sha} for path, sha in fresh],
                    outputs=[name for _, name in outputs],
                )
                self._notify(
                    run_id=run_id,
                    status="STAGED",
                    files=[name for _, name in outputs],
                    destination=str(staging),
                    summary=summary,
                )
            return {
                "run_id": run_id,
                "job": job,
                "outputs": [name for _, name in outputs],
                "skipped": skipped,
                "status": "STAGED",
                "staging": str(staging),
            }
        except Exception as e:
            # Keep the staging dir for inspection; never stage a bad file.
            # A dry run leaves nothing behind either way -- the caller
            # discards the temporary staging, failed or not.
            self._notify(run_id=run_id, status="FAILED", files=[], error=str(e))
            log.error("run %s FAILED: %s (staging kept at %s)", run_id, e, staging)
            raise
