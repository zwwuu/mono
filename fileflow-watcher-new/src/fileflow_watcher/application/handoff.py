"""Application -- handoff use cases (forward flow).

The watcher is a courier plus a completion-watcher: it copies
verified manifest batches from FTP 1 to FTP 2 for external
processing, then acts when the processor finishes each file.

Handoff (one manifest batch found in FTP 1 ``incoming/<job>/``):
  1. READ    -- download and parse the manifest. An unknown job
               is dead-lettered to failed/<run_id>/ + reason.txt.
  2. DEDUP   -- each file is checked against the ledger (job +
               name + sha256 in a SENT_TO_PROCESSING or COMPLETED
               run). Already recorded -> moved straight to FTP 1
               done/<job>/, no copy, no email. A changed file
               (new hash) copies as a new run; a FAILED outcome
               does not block a re-copy.
  3. WAIT    -- every fresh file must have landed (the agent
               uploads data first, manifest last). A lone
               manifest means the agent's upload is still in
               flight. A manifest with no files at all is
               malformed; leave it for the operator.
  4. VERIFY  -- re-compute SHA-256 + size against the manifest
               for the fresh files (and any duplicate still
               sitting in incoming/). Mismatch ->
               quarantine/<run_id>/ + reason.txt, ledger
               QUARANTINED. A corrupt file never reaches FTP 2.
  5. RECORD  -- the run is SENT_TO_PROCESSING with the file list
               (name + sha256 + size) in its detail, BEFORE any
               copy. A crash after this point is recoverable: the
               next scan resumes the copy instead of starting over.
  6. COPY    -- the fresh files go to FTP 2 processing/<job>/
               via the transport (.part + rename, size- and
               hash-verified, concurrently when more than one).
               The manifest is NOT copied: it is the
               agent<->watcher contract, not the processor's input.
               Per file, a copy is skipped when FTP 2 already
               holds the same name with the same SHA-256 (left by
               an earlier pass of the same batch). Size alone is
               not proof -- a changed file can keep its size -- so
               a matching size is confirmed by downloading the
               remote file back and hashing it.

A run already SENT_TO_PROCESSING is resumed by the handoff scan
(_resume_copy): files still missing on FTP 2 are downloaded from
FTP 1, checked against the recorded SHA-256, and copied; files
the processor already finished (in FTP 2 success/ or failed/,
or with a recorded outcome) are left for the completion scan.
The completion scan owns a run once every file is on FTP 2.

Completion (FTP 2 ``<job>/success/`` and ``<job>/failed/``):
  1. MATCH   -- each finished file is matched by name to a
               recorded SENT_TO_PROCESSING run, then verified by
               SHA-256 read-back. Unknown files are left alone
               with a warning, never deleted.
  2. ARCHIVE -- the original on FTP 1 moves to done/<job>/ or
               failed/<job>/, matching the outcome. A hash
               mismatch on a finished file is a FAILED outcome.
  3. FINISH  -- when every file of a run has an outcome, the run
               becomes COMPLETED (all success) or FAILED (any
               failed), the manifest moves to FTP 1 done/<job>/
               or failed/<job>/, and ONE email is sent for the
               batch (success summary, or failure naming the
               failed files).

A run left RECEIVED by a crashed handoff is re-processed on the
next scan.
"""

from __future__ import annotations

import json
import tempfile
import time
from collections.abc import Callable
from dataclasses import replace as dataclass_replace
from datetime import datetime, timezone
from pathlib import Path

from fileflow_contract import (
    Manifest,
    file_already_recorded,
    read_manifest,
    sha256_file,
    utcnow_iso,
    verify_files,
)

from ..domain.entities import RunRecord
from ..domain.ports import RunRepository, Transport
from ..infrastructure.config import WatcherConfig
from ..infrastructure.filesystem import PART_SUFFIX, log
from ..infrastructure.notify import Notifier
from ..infrastructure.retry import with_retries


def _manifest_name(run_id: str) -> str:
    return f"{run_id}.manifest.json"


def _parse_sent_at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


class HandoffUseCase:
    def __init__(
        self,
        cfg: WatcherConfig,
        *,
        repository: RunRepository,
        intake: Transport,
        processing: Transport,
        sleep: Callable[[float], None] | None = None,
    ):
        self.cfg = cfg
        self.repository = repository
        self.intake = intake
        self.processing = processing
        self._sleep = sleep or time.sleep

    # -- small helpers ---------------------------------------------------
    def _incoming_dir(self, job: str) -> str:
        return f"{self.cfg.intake.incoming_dir}/{job}"

    def _done_dir(self, job: str) -> str:
        return f"{self.cfg.intake.done_dir}/{job}"

    def _failed_dir(self, job: str) -> str:
        return f"{self.cfg.intake.failed_dir}/{job}"

    def _dead_letter(
        self, run_id: str, job: str, names: list[str], reason: str, state: str, subdir: str
    ) -> None:
        """Move a batch out of incoming/ into quarantine/<run_id>/
        or failed/<run_id>/, attach the reason, record the state."""
        dest = f"{subdir}/{run_id}"
        self.intake.makedirs(dest)
        for name in names:
            try:
                self.intake.move(self._incoming_dir(job), name, dest, name)
            except OSError as e:
                log.warning("run %s: could not move %s to %s: %s", run_id, name, dest, e)
        with tempfile.TemporaryDirectory() as tmp:
            reason_path = Path(tmp) / "reason.txt"
            reason_path.write_text(reason, encoding="utf-8")
            try:
                self.intake.upload_verified(reason_path, dest, "reason.txt")
            except (OSError, ValueError) as e:
                log.warning("run %s: could not write reason.txt: %s", run_id, e)
        self.repository.set_state(run_id, state, reason=reason)
        log.error("run %s %s: %s", run_id, state, reason)

    def _remote_matches(
        self,
        name: str,
        expected_sha: str | None,
        expected_size: int | None,
        remote_dir: str,
        scratch: Path,
    ) -> bool:
        """True when FTP 2 already holds `name` with SHA-256
        `expected_sha` (the recorded hash for the file).

        Size is only a fast path: a different size cannot match,
        so no download is needed. A matching size proves nothing
        -- a changed file can keep its size -- so the remote file
        is downloaded back and hashed. A missing or unreadable
        remote file is not a match: the copy goes ahead.
        """
        if expected_sha is None:
            return False
        remote_size = self.processing.remote_size(remote_dir, name)
        if remote_size is None:
            return False
        if expected_size is not None and remote_size != expected_size:
            return False
        probe = scratch / f".verify-{name}"
        try:
            with_retries(
                lambda: self.processing.download(remote_dir, name, probe),
                sleep=self._sleep,
            )
        except OSError as e:
            log.warning("could not read back %s from FTP 2 to verify it: %s; re-copying", name, e)
            return False
        return sha256_file(probe) == expected_sha

    def _resume_copy(self, run_id: str, job: str, record: RunRecord, started: float) -> str | None:
        """Resume a run recorded SENT_TO_PROCESSING by a crashed
        pass: copy any file still missing on FTP 2. Returns
        "SENT_TO_PROCESSING" when anything was copied, else None.

        A file is skipped when it already has a recorded outcome,
        when the processor already finished it (sitting in FTP 2
        success/ or failed/, awaiting the completion scan), or when
        FTP 2 processing/ already holds it with the same SHA-256
        (a matching size is confirmed by downloading the remote
        file back and hashing it; size alone is not proof). A file
        downloaded from FTP 1 is checked against the recorded
        SHA-256 before it is copied: the file on FTP 1 may be a
        newer batch's same-named file, and wrong content must never
        be copied for the old run.
        """
        files = record.detail.get("files", [])
        outcomes = record.detail.get("outcomes", {})
        processing_dir = f"{self.cfg.processing.processing_dir}/{job}"
        finished = set(self.processing.list_names(f"{self.cfg.processing.success_dir}/{job}"))
        finished |= set(self.processing.list_names(f"{self.cfg.processing.failed_dir}/{job}"))
        incoming = self._incoming_dir(job)
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            missing: list[tuple[Path, str]] = []
            for f in files:
                name = f.get("name")
                if name is None or name in outcomes or name in finished:
                    continue
                if self._remote_matches(
                    name, f.get("sha256"), f.get("size"), processing_dir, workdir
                ):
                    continue
                local = workdir / name
                try:
                    with_retries(
                        lambda: self.intake.download(incoming, name, local),
                        sleep=self._sleep,
                    )
                except OSError as e:
                    log.error("run %s: resume download of %s failed: %s", run_id, name, e)
                    return None
                if sha256_file(local) != f.get("sha256"):
                    log.warning(
                        "run %s: %s on FTP 1 no longer matches the recorded hash, "
                        "left for the operator",
                        run_id,
                        name,
                    )
                    continue
                missing.append((local, name))
            if not missing:
                return None
            try:
                self.processing.makedirs(processing_dir)
                with_retries(
                    lambda: self.processing.upload_many(missing, processing_dir),
                    sleep=self._sleep,
                )
            except (OSError, ValueError) as e:
                log.error("run %s: resume copy to FTP 2 failed: %s", run_id, e)
                return None
            total_bytes = sum(Path(local).stat().st_size for local, _ in missing)
            summary = (
                f"run {run_id} job={job} SENT_TO_PROCESSING "
                f"{len(missing)} file(s), {total_bytes} bytes in "
                f"{time.monotonic() - started:.1f}s -> FTP 2 {processing_dir}"
            )
            log.info(summary)
            return "SENT_TO_PROCESSING"

    # -- the use case ----------------------------------------------------
    def handoff(self, job: str, manifest_name: str) -> str | None:
        """Hand off one manifest batch found in incoming/<job>/.

        Returns the final state, or None when the batch was already
        seen / is not ready yet (leave it in place).
        """
        started = time.monotonic()
        incoming = self._incoming_dir(job)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            manifest_local = tmp_path / manifest_name
            try:
                with_retries(
                    lambda: self.intake.download(incoming, manifest_name, manifest_local),
                    sleep=self._sleep,
                )
                manifest = Manifest.from_dict(read_manifest(manifest_local))
            except (OSError, KeyError, ValueError, json.JSONDecodeError) as e:
                log.warning("incoming %s/%s unreadable, skipped: %s", job, manifest_name, e)
                return None
        run_id = manifest.run_id
        batch_job = manifest.job
        names = [f.name for f in manifest.files]

        record = self.repository.get(run_id)
        resuming = record is not None and record.state == "SENT_TO_PROCESSING"
        if record is not None and record.state in ("FAILED", "QUARANTINED"):
            return None  # terminal; a genuine retry is a new run_id
        if resuming:
            # The record exists because VERIFY passed on the first
            # pass: never re-verify against the manifest here. The
            # file on FTP 1 may since have been replaced by a newer
            # batch with the same name; re-verifying would falsely
            # quarantine a run whose copy already landed on FTP 2.
            assert record is not None
            return self._resume_copy(run_id, batch_job, record, started)

        # Unknown job: do not guess. Dead-letter it with the reason.
        if batch_job not in self.cfg.jobs:
            if record is None:
                self.repository.claim(
                    run_id=run_id, job=batch_job, idempotency_key=manifest.idempotency_key
                )
            self._dead_letter(
                run_id,
                job,
                [*names, manifest_name],
                f"unknown job {batch_job!r}",
                "FAILED",
                self.cfg.intake.failed_dir,
            )
            return "FAILED"

        # A manifest with no files at all is malformed; leave it for
        # the operator instead of crashing the scan.
        if not manifest.files:
            log.warning("run %s: manifest lists no files, left for the operator", run_id)
            return None

        # 2. DEDUP -- per file, against completed handoffs. Only
        #    SENT_TO_PROCESSING and COMPLETED runs count: a FAILED
        #    outcome does not block a re-copy.
        recorded = [
            r
            for r in list(self.repository.list_by_state("SENT_TO_PROCESSING"))
            + list(self.repository.list_by_state("COMPLETED"))
            if r.run_id != run_id
        ]
        fresh = []
        duplicates = []
        for entry in manifest.files:
            if file_already_recorded(recorded, batch_job, entry.name, entry.sha256):
                duplicates.append(entry.name)
            else:
                fresh.append(entry.name)
        for name in duplicates:
            try:
                self.intake.move(incoming, name, self._done_dir(batch_job), name)
            except OSError as e:
                # The file may already have been archived by a
                # crashed pass; the move failing is not fatal.
                log.warning("run %s: could not archive duplicate %s: %s", run_id, name, e)
            else:
                log.warning("run %s: %s already processed, archived without re-copy", run_id, name)
        if not fresh:
            try:
                self.intake.move(incoming, manifest_name, self._done_dir(batch_job), manifest_name)
            except OSError as e:
                log.warning("run %s: could not archive manifest: %s", run_id, e)
            return "DUPLICATE"

        # 3. WAIT -- every fresh file must have landed. A lone
        #    manifest means the agent's upload is still in flight.
        present = set(self.intake.list_names(incoming))
        if any(name not in present for name in fresh):
            return None

        # 4. VERIFY -- download the batch and check it against the
        #    manifest before anything is copied to FTP 2. Duplicates
        #    still sitting in incoming/ are checked too; ones
        #    already archived were verified on their first pass.
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            to_verify = fresh + [name for name in duplicates if name in present]
            try:
                with_retries(
                    lambda: [
                        self.intake.download(incoming, name, workdir / name) for name in to_verify
                    ],
                    sleep=self._sleep,
                )
            except OSError as e:
                log.error("run %s: download from FTP 1 failed: %s", run_id, e)
                return None
            # Only the files actually downloaded are checked: a
            # duplicate already archived to done/ is not in the
            # workdir, and must not read as a missing file.
            checked = dataclass_replace(
                manifest,
                files=[e for e in manifest.files if e.name in to_verify],
            )
            problems = verify_files(workdir, checked)
            if problems:
                if record is None:
                    self.repository.claim(
                        run_id=run_id, job=batch_job, idempotency_key=manifest.idempotency_key
                    )
                self._dead_letter(
                    run_id,
                    job,
                    [*names, manifest_name],
                    "; ".join(problems),
                    "QUARANTINED",
                    "quarantine",
                )
                return "QUARANTINED"

            if record is None:
                self.repository.claim(
                    run_id=run_id, job=batch_job, idempotency_key=manifest.idempotency_key
                )

            # 5. RECORD -- SENT_TO_PROCESSING with the file list,
            #    BEFORE any copy. A crash after this point leaves a
            #    record the next scan resumes from.
            self.repository.set_state(
                run_id,
                "SENT_TO_PROCESSING",
                files=[
                    {
                        "name": entry.name,
                        "sha256": entry.sha256,
                        "size": entry.size,
                    }
                    for entry in manifest.files
                    if entry.name in fresh
                ],
                duplicates=duplicates,
                sent_at=utcnow_iso(),
            )

            # 6. COPY -- fresh files to FTP 2, .part + rename,
            #    size- and hash-verified. A file is skipped when
            #    FTP 2 already holds it with the same SHA-256
            #    (left by a crashed pass; a matching size is
            #    confirmed by download-back hashing, since a
            #    changed file can keep its size). The manifest
            #    stays behind on FTP 1.
            processing_dir = f"{self.cfg.processing.processing_dir}/{batch_job}"
            entries = {entry.name: entry for entry in manifest.files}
            missing: list[tuple[Path, str]] = []
            for name in fresh:
                if self._remote_matches(
                    name,
                    entries[name].sha256,
                    entries[name].size,
                    processing_dir,
                    workdir,
                ):
                    continue
                missing.append((workdir / name, name))
            if missing:
                try:
                    self.processing.makedirs(processing_dir)
                    with_retries(
                        lambda: self.processing.upload_many(missing, processing_dir),
                        sleep=self._sleep,
                    )
                except (OSError, ValueError) as e:
                    log.error("run %s: copy to FTP 2 failed: %s", run_id, e)
                    return None
                total_bytes = sum(entries[name].size for _, name in missing)
                summary = (
                    f"run {run_id} job={batch_job} SENT_TO_PROCESSING "
                    f"{len(missing)} file(s), {total_bytes} bytes in "
                    f"{time.monotonic() - started:.1f}s -> FTP 2 {processing_dir}"
                )
                log.info(summary)
            log.info(
                "run %s handed off %d file(s) to FTP 2 %s (%d duplicate(s) skipped)",
                run_id,
                len(fresh),
                processing_dir,
                len(duplicates),
            )
            return "SENT_TO_PROCESSING"


class CompletionUseCase:
    def __init__(
        self,
        cfg: WatcherConfig,
        *,
        repository: RunRepository,
        intake: Transport,
        processing: Transport,
        notifier: Notifier,
        sleep: Callable[[float], None] | None = None,
    ):
        self.cfg = cfg
        self.repository = repository
        self.intake = intake
        self.processing = processing
        self.notifier = notifier
        self._sleep = sleep or time.sleep

    def complete(self) -> dict[str, int]:
        """One completion pass: poll FTP 2 success/ + failed/ for
        every job, act on finished files. Returns final-state counts."""
        counts: dict[str, int] = {}
        runs = self.repository.list_by_state("SENT_TO_PROCESSING")
        if not runs:
            return counts
        for job in self.cfg.jobs:
            job_runs = [r for r in runs if r.job == job]
            if not job_runs:
                continue
            for outcome_dir, outcome in (
                (self.cfg.processing.success_dir, "success"),
                (self.cfg.processing.failed_dir, "failed"),
            ):
                remote_dir = f"{outcome_dir}/{job}"
                for name in sorted(self.processing.list_names(remote_dir)):
                    if name.endswith(PART_SUFFIX) or name.endswith(".manifest.json"):
                        continue
                    state = self._complete_one(job, job_runs, outcome, remote_dir, name)
                    if state:
                        counts[state] = counts.get(state, 0) + 1
        return counts

    def _complete_one(
        self,
        job: str,
        runs: list[RunRecord],
        outcome: str,
        remote_dir: str,
        name: str,
    ) -> str | None:
        """Act on one finished file. Returns the run's final state
        when this file completes its batch, else None."""
        candidates = [
            r for r in runs if any(f.get("name") == name for f in r.detail.get("files", []))
        ]
        if not candidates:
            log.warning("processing %s/%s: no recorded run, left alone", remote_dir, name)
            return None
        # Verify by SHA-256 read-back before trusting the outcome.
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / name
            try:
                with_retries(
                    lambda: self.processing.download(remote_dir, name, local),
                    sleep=self._sleep,
                )
            except OSError as e:
                log.error("processing %s/%s: download failed: %s", remote_dir, name, e)
                return None
            actual_sha = sha256_file(local)
        exact = [
            r
            for r in candidates
            if any(
                f.get("name") == name and f.get("sha256") == actual_sha
                for f in r.detail.get("files", [])
            )
        ]
        if exact:
            run = exact[-1]  # list_by_state orders by updated_at: newest last
            final_outcome = outcome
            reason = None
        elif len(candidates) == 1:
            run = candidates[0]
            final_outcome = "failed"
            expected = next(
                f.get("sha256") for f in run.detail.get("files", []) if f.get("name") == name
            )
            reason = (
                f"hash mismatch: finished file {name} sha256 {actual_sha} != recorded {expected}"
            )
        else:
            log.warning(
                "processing %s/%s: %d runs claim this name, hash matches none; left alone",
                remote_dir,
                name,
                len(candidates),
            )
            return None

        run_id = run.run_id
        # Re-fetch: an earlier file in this same pass may already
        # have recorded its outcome -- the record passed in is stale.
        fresh = self.repository.get(run_id)
        if fresh is None:
            log.warning("run %s vanished from the ledger; outcome not recorded", run_id)
            return None
        outcomes = {k: dict(v) for k, v in fresh.detail.get("outcomes", {}).items()}
        if name in outcomes:
            return None  # already recorded; the finished file stays put
        outcomes[name] = {"outcome": final_outcome}
        if reason:
            outcomes[name]["reason"] = reason

        # Archive the original on FTP 1, matching the outcome.
        dest_dir = (
            self.cfg.intake.done_dir if final_outcome == "success" else self.cfg.intake.failed_dir
        )
        incoming = f"{self.cfg.intake.incoming_dir}/{job}"
        if self.intake.remote_size(incoming, name) is not None:
            try:
                with_retries(
                    lambda: self.intake.move(incoming, name, f"{dest_dir}/{job}", name),
                    sleep=self._sleep,
                )
            except OSError as e:
                log.warning("run %s: could not archive %s: %s", run_id, name, e)
        else:
            log.warning("run %s: original %s already gone from FTP 1", run_id, name)
        log.info("run %s: %s -> %s", run_id, name, final_outcome)

        files = fresh.detail.get("files", [])
        self.repository.set_state(run_id, "SENT_TO_PROCESSING", outcomes=outcomes)
        if not all(f.get("name") in outcomes for f in files):
            return None  # more files still in flight
        return self._finish(run_id, job, files, outcomes)

    def _finish(self, run_id: str, job: str, files: list, outcomes: dict) -> str:
        """Every file has an outcome: finalize the run, move the
        manifest, and send the one email for the batch."""
        failed = {
            name: entry for name, entry in outcomes.items() if entry.get("outcome") == "failed"
        }
        all_ok = not failed
        final_state = "COMPLETED" if all_ok else "FAILED"
        manifest_name = _manifest_name(run_id)
        dest_dir = self.cfg.intake.done_dir if all_ok else self.cfg.intake.failed_dir
        incoming = f"{self.cfg.intake.incoming_dir}/{job}"
        if self.intake.remote_size(incoming, manifest_name) is not None:
            try:
                with_retries(
                    lambda: self.intake.move(
                        incoming, manifest_name, f"{dest_dir}/{job}", manifest_name
                    ),
                    sleep=self._sleep,
                )
            except OSError as e:
                log.warning("run %s: could not archive manifest: %s", run_id, e)
        else:
            log.warning("run %s: manifest already gone from FTP 1", run_id)
        self.repository.set_state(run_id, final_state, outcomes=outcomes)
        log.info(
            "run %s %s: %d file(s), %d failed",
            run_id,
            final_state,
            len(files),
            len(failed),
        )
        record = self.repository.get(run_id)
        sent_at = _parse_sent_at(record.detail.get("sent_at")) if record else None
        if sent_at is not None:
            duration = (datetime.now(timezone.utc) - sent_at).total_seconds()
        elif record is not None:
            duration = (datetime.now(timezone.utc) - record.updated_at).total_seconds()
        else:
            duration = 0.0
        total_bytes = sum(int(f.get("size") or 0) for f in files)
        summary = (
            f"run {run_id} job={job} {final_state} {len(files)} file(s), "
            f"{total_bytes} bytes in {duration:.1f}s -> FTP 1 {dest_dir}/{job}"
        )
        log.info(summary)
        lines = [
            summary,
            f"job: {job}",
            f"run: {run_id}",
            f"files: {len(files)}",
        ]
        for f in files:
            entry = outcomes.get(f["name"], {})
            line = f"  {entry.get('outcome', '?')}: {f['name']}"
            if entry.get("reason"):
                line += f" ({entry['reason']})"
            lines.append(line)
        subject = (
            f"{job} {run_id}: {'completed' if all_ok else 'FAILED'} "
            f"({len(files) - len(failed)} ok, {len(failed)} failed)"
        )
        self.notifier.notify(subject, "\n".join(lines))
        return final_state
