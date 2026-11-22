"""Application -- download + upload use case (forward flow).

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
     folder does not upload them again. A changed file (new hash)
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
  5. upload every output file, then the manifest *last*, via .part +
     rename, to ``incoming/<job>/`` on the FTP server;
  6. verify the remote sizes, then delete the staging copy.
     The staging copy is kept until the upload is verified -- the
     sender never deletes before the receiver has the bytes.
     The run is recorded UPLOADED with the list of files it
     uploaded (job + name + hash each), which is what the next
     run's skip check reads.

The use case only talks to ports (Source, Transport, RunRepository)
and to the procedure registry. It never opens a socket or a database
itself, so it can be tested with a local transport and a throwaway
SQLite file -- which is exactly what the test suite does.
"""

from __future__ import annotations

import shutil
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

from ..domain.integrity import render_name
from ..domain.ports import RunRepository, Source, Transport
from ..domain.procedures import get_procedure
from ..infrastructure.filesystem import log, read_csv_rows


def validate_input(path: Path, job_cfg: dict) -> None:
    if path.stat().st_size == 0:
        raise ValueError(f"downloaded file is empty: {path.name}")
    if job_cfg.get("expect_csv"):
        rows = read_csv_rows(path)
        if not any(c.strip() for c in rows[0]):
            raise ValueError(f"not a parseable CSV: {path.name}")


class DownloadAndUploadUseCase:
    def __init__(
        self,
        cfg: dict,
        *,
        source: Source,
        transport: Transport,
        repository: RunRepository,
        procedure_lookup=get_procedure,
    ):
        self.cfg = cfg
        self.source = source
        self.transport = transport
        self.repository = repository
        self.procedure_lookup = procedure_lookup

    def run(self) -> dict:
        cfg = self.cfg
        job = cfg["job"]
        run_id = new_run_id()
        staging = Path(cfg["staging_dir"]) / run_id
        staging.mkdir(parents=True, exist_ok=True)
        log.info("run %s job=%s starting", run_id, job)
        try:
            # 1-2. download, split off already-processed files, validate
            # only the fresh ones. A changed file has a new hash, so it
            # is fresh even under the same name.
            downloaded = self.source.download(staging, run_id)
            if not downloaded:
                raise ValueError("source produced no files")
            fresh: list[tuple[Path, str]] = []
            skipped: list[str] = []
            for path in downloaded:
                input_sha = sha256_file(path)
                # Only UPLOADED runs count: a file from a failed run
                # is fresh and retries.
                if file_already_recorded(
                    self.repository.list_by_state("UPLOADED"), job, path.name, input_sha
                ):
                    log.info("run %s: %s already processed, skipped", run_id, path.name)
                    skipped.append(path.name)
                    continue
                validate_input(path, cfg)
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
                }

            # 3. first rename / format each fresh file (agent-side procedure)
            proc_name = cfg.get("agent_procedure", "rename-only")
            proc = self.procedure_lookup(proc_name)
            outputs: list[tuple[Path, str]] = []
            for path, _ in fresh:
                formatted = proc.fn(path, staging, {"job": job, "run_id": run_id})
                stem, ext = (
                    formatted.name.rsplit(".", 1) if "." in formatted.name else (formatted.name, "")
                )
                ext = f".{ext}" if ext else ""
                out_name = render_name(
                    cfg.get("rename_pattern", "{date}_{name}{ext}"),
                    name=stem,
                    ext=ext,
                    job=job,
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
                    "source_url": cfg["source"].get("url")
                    or cfg["source"].get("path")
                    or cfg["source"].get("dir"),
                    "agent_procedure": proc_name,
                    "agent_procedure_version": proc.version,
                    # Input hashes are provenance / idempotency only: the
                    # input files themselves are NOT uploaded, so they must
                    # not appear in "files" -- the watcher verifies only the
                    # files listed there, i.e. the transferred outputs.
                },
            )
            manifest_path = staging / f"{run_id}.manifest.json"
            write_manifest(manifest_path, manifest)

            # 5-6. upload every output first, manifest last; verify; clean up
            remote_dir = f"incoming/{job}"
            self.transport.upload_many_with_manifest(outputs, manifest_path, remote_dir)
            self.repository.claim(
                run_id=run_id,
                job=job,
                idempotency_key=manifest.idempotency_key,
            )
            # Record the uploaded files on the run: the next run's skip
            # check reads this list.
            self.repository.set_state(
                run_id,
                "UPLOADED",
                files=[{"name": path.name, "sha256": sha} for path, sha in fresh],
                outputs=[name for _, name in outputs],
            )
            log.info(
                "run %s uploaded %d file(s) to %s",
                run_id,
                len(outputs),
                remote_dir,
            )
            shutil.rmtree(staging)
            return {
                "run_id": run_id,
                "job": job,
                "outputs": [name for _, name in outputs],
                "skipped": skipped,
                "status": "UPLOADED",
            }
        except Exception as e:
            # Keep the staging dir for inspection; never upload a bad file.
            log.error("run %s FAILED: %s (staging kept at %s)", run_id, e, staging)
            raise
