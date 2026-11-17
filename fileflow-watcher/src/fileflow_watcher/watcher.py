"""FileFlow Watcher -- runs ON the FTP server, the only machine that
can see the share drive.

It does NOT use FTP to read files: on the FTP server the incoming
directories are local paths (e.g. /srv/ftp/incoming). It polls them,
and for each manifest it finds:

  1. CLAIM   -- atomic move of data file + manifest from
                incoming/<job>/ to processing/<run_id>/ . A second
                watcher / a restart can never claim the same file twice,
                because the rename is atomic and the source is gone.
  2. VERIFY  -- re-compute SHA-256 + size against the manifest.
                Mismatch -> quarantine/<run_id>/, ledger QUARANTINED,
                alert. Nothing is processed, nothing is published.
  3. DEDUP   -- ledger.claim(idempotency_key). Already seen ->
                duplicate/ , ledger DUPLICATE, no re-run, no re-publish.
  4. ROUTE   -- job comes from the manifest / incoming directory.
                Unknown job, or a filename that matches no rule ->
                failed/<run_id>/ + reason.txt. Never guess a procedure.
  5. PROCESS -- run the job's registered procedure in the workdir.
                It writes a NEW output file; the input is never edited.
  6. VERIFY OUTPUT -- exists, non-empty, parses if CSV.
  7. PUBLISH -- copy output + final manifest to the share drive via
                .part + rename, then READ BACK the share copy and
                compare SHA-256 + size. Only then is the run PUBLISHED.
  8. ARCHIVE -- the verified input + manifest move to done/<run_id>/.

Failure handling: a procedure/publish exception -> failed/<run_id>/
with reason.txt, ledger FAILED, input kept. A later re-upload with
the same run_id + input hash is a duplicate, not a retry; a genuine
retry is a NEW run_id from the agent (new idempotency key).

Run:  python -m fileflow_watcher.watcher --config config/watcher.json
      python -m fileflow_watcher.watcher --config config/watcher.json --loop 10
      python -m fileflow_watcher.watcher --config config/watcher.json --reconcile
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

from .common import (
    MANIFEST_VERSION, PART_SUFFIX, atomic_move, atomic_publish, file_entry,
    log, new_run_id, read_manifest, setup_logging, sha256_file, utcnow_iso,
    verify_files, write_manifest,
)
from .ledger import Ledger
from .procedures import get_procedure


def load_config(path: str | Path) -> dict:
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    # Optional local procedures file: extra business procedures that
    # live next to the config, imported without touching this watcher.
    extra = cfg.get("procedures_module")
    if extra:
        __import__(extra)
    return cfg


def _move_tree(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for p in src.iterdir():
        shutil.move(str(p), str(dst / p.name))
    src.rmdir()


def _quarantine(ftp_root: Path, run_dir: Path, run_id: str, reason: str,
                ledger: Ledger) -> None:
    dest = ftp_root / "quarantine" / run_id
    _move_tree(run_dir, dest)
    (dest / "reason.txt").write_text(reason, encoding="utf-8")
    ledger.set_state(run_id, "QUARANTINED", reason=reason)
    log.error("run %s QUARANTINED: %s", run_id, reason)


def _fail(ftp_root: Path, run_dir: Path, run_id: str, reason: str,
          ledger: Ledger) -> None:
    dest = ftp_root / "failed" / run_id
    _move_tree(run_dir, dest)
    (dest / "reason.txt").write_text(reason, encoding="utf-8")
    ledger.set_state(run_id, "FAILED", reason=reason)
    log.error("run %s FAILED: %s", run_id, reason)


def process_manifest(cfg: dict, manifest_path: Path, ledger: Ledger) -> str:
    """Process one manifest found in incoming/<job>/. Returns final state."""
    ftp_root = Path(cfg["ftp_root"])
    share_root = Path(cfg["share_root"])
    manifest = read_manifest(manifest_path)
    run_id = manifest["run_id"]
    job = manifest["job"]
    job_cfg = cfg["jobs"].get(job)
    if job_cfg is None:
        # Unknown job: do not guess. Claim it so it is not re-scanned,
        # then dead-letter it with the reason attached.
        run_dir = ftp_root / "processing" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        for p in manifest_path.parent.glob(f"{run_id}*"):
            pass  # files are moved below by name, not by glob
        data_name = next(
            (f["name"] for f in manifest["files"] if f["role"] == "output"), None)
        for name in [data_name, manifest_path.name]:
            if name and (manifest_path.parent / name).exists():
                atomic_move(manifest_path.parent / name, run_dir / name)
        ledger.claim(run_id=run_id, job=job,
                     idempotency_key=manifest["idempotency_key"],
                     input_sha256=manifest.get("input_sha256"))
        _fail(ftp_root, run_dir, run_id, f"unknown job {job!r}", ledger)
        return "FAILED"

    # 1. CLAIM -- atomic move into processing/<run_id>/
    run_dir = ftp_root / "processing" / run_id
    if run_dir.exists():
        return "RECEIVED"  # already claimed by another pass; leave it
    run_dir.mkdir(parents=True, exist_ok=True)
    data_name = next(f["name"] for f in manifest["files"] if f["role"] == "output")
    for name in (data_name, manifest_path.name):
        src = manifest_path.parent / name
        if src.exists():
            atomic_move(src, run_dir / name)
    is_new = ledger.claim(run_id=run_id, job=job,
                          idempotency_key=manifest["idempotency_key"],
                          input_sha256=manifest.get("input_sha256"))

    # 2. VERIFY -- manifest vs the bytes that actually arrived
    problems = verify_files(run_dir, manifest)
    if problems:
        _quarantine(ftp_root, run_dir, run_id, "; ".join(problems), ledger)
        return "QUARANTINED"

    # 3. DEDUP -- after verification, but BEFORE any set_state, or a
    #    re-upload would overwrite the PUBLISHED state with VERIFIED
    #    and re-publish. The idempotency key embeds run_id, so a
    #    duplicate is the same run_id re-appearing (re-upload / crash
    #    retry) after publishing.
    if not is_new and ledger.get(run_id)["state"] == "PUBLISHED":
        dest = ftp_root / "duplicate" / run_id
        _move_tree(run_dir, dest)
        # Do NOT change the ledger state: it must stay PUBLISHED, so
        # reconcile still checks the share copy, and a third re-upload
        # is still recognised as a duplicate.
        log.warning("run %s duplicate, already published; skipped", run_id)
        return "DUPLICATE"
    ledger.set_state(run_id, "VERIFIED")
    log.info("run %s verified %s", run_id, data_name)

    # 4. PROCESS
    ledger.set_state(run_id, "PROCESSING")
    workdir = run_dir / "work"
    workdir.mkdir(exist_ok=True)
    proc = get_procedure(job_cfg["procedure"])
    try:
        output = proc["fn"](run_dir / data_name, workdir,
                            {"job": job, "run_id": run_id})
        if not output.exists() or output.stat().st_size == 0:
            raise ValueError(f"procedure produced no output: {output.name}")
        if job_cfg.get("expect_csv"):
            import csv
            with open(output, newline="", encoding="utf-8-sig") as f:
                rows = list(csv.reader(f))
            if not rows:
                raise ValueError("procedure output is not parseable CSV")
    except (ValueError, OSError, Exception) as e:
        _fail(ftp_root, run_dir, run_id, f"procedure {job_cfg['procedure']}: {e}", ledger)
        return "FAILED"
    ledger.set_state(run_id, "PROCESSED")
    log.info("run %s processed by %s -> %s", run_id, job_cfg["procedure"], output.name)

    # 5. PUBLISH to the share drive (only this machine can see it)
    #    .part + rename, then read-back verification.
    out_name = job_cfg.get("output_pattern", "{name}").format(
        name=output.name, run_id=run_id, job=job)
    share_dir = share_root / job_cfg.get("share_dir", job)
    final_manifest = {
        "version": manifest["version"],
        "run_id": run_id,
        "job": job,
        "created_at": utcnow_iso(),
        "source_run": manifest,
        "procedure": job_cfg["procedure"],
        "procedure_version": proc["version"],
        "files": [file_entry(output, "output")],
    }
    try:
        published = atomic_publish(output, share_dir / out_name)
        manifest_name = f"{Path(out_name).stem}.manifest.json"
        write_manifest(share_dir / manifest_name, final_manifest)
        # read-back: the share copy must hash exactly like the output
        if sha256_file(published) != final_manifest["files"][0]["sha256"]:
            raise IOError(f"read-back mismatch on share for {out_name}")
    except (OSError, IOError, Exception) as e:
        _fail(ftp_root, run_dir, run_id, f"publish to share: {e}", ledger)
        return "FAILED"

    # 6. ARCHIVE the verified input on the FTP server
    ledger.set_state(run_id, "PUBLISHED", published=str(published))
    done = ftp_root / "done" / run_id
    _move_tree(run_dir, done)
    log.info("run %s PUBLISHED to %s", run_id, published)
    return "PUBLISHED"


def scan_outbound_once(cfg: dict, ledger: Ledger) -> dict[str, int]:
    """Reverse flow: SMB share -> FTP outgoing, waiting for the agent.

    Watches ``<share_root>/<requests_dir>/<job>/`` for new files.
    A file is only picked when it is stable: not a .part, and its
    mtime is at least ``min_age_seconds`` old (default 2s), so a
    writer still copying the file is never picked up mid-write.
    (Drop writers should still write .part + rename when they can.)

    For each stable file, in order:
      1. seal it: new run_id, manifest with SHA-256 + size;
      2. copy data + manifest to ``<ftp_root>/outgoing/<job>/``
         via .part + rename, manifest last (same trigger contract);
      3. verify the outgoing copy hashes exactly like the SMB file;
      4. only then move the SMB original to
         ``<share_root>/<archive_dir>/<job>/`` -- the move is what
         stops it being picked twice. The archive stays on the share,
         which only this machine can see.
    A file with the same job + name + hash seen before is a duplicate:
    it is archived without a second outgoing copy.
    Returns counts, e.g. {"OUTBOUND_READY": 2}.
    """
    outbound_cfg = cfg.get("outbound")
    if not outbound_cfg:
        return {}
    ftp_root = Path(cfg["ftp_root"])
    share_root = Path(cfg["share_root"])
    requests_dir = share_root / outbound_cfg.get("requests_dir", "upload-requests")
    archive_dir = share_root / outbound_cfg.get("archive_dir", "upload-archive")
    min_age = outbound_cfg.get("min_age_seconds", 2)
    now = time.time()
    counts: dict[str, int] = {}
    for job_dir in sorted(requests_dir.iterdir()) if requests_dir.is_dir() else []:
        job = job_dir.name
        job_cfg = cfg["jobs"].get(job)
        if job_cfg is None or not job_cfg.get("outbound"):
            continue
        for src in sorted(job_dir.iterdir()):
            if (not src.is_file() or src.suffix == PART_SUFFIX
                    or src.name.endswith(".manifest.json")):
                continue
            if now - src.stat().st_mtime < min_age:
                continue  # still being written; try next scan
            if src.stat().st_size == 0:
                log.warning("outbound %s: empty file, skipped", src.name)
                continue
            input_sha = sha256_file(src)
            key = f"outbound|{job}|{input_sha}|{src.name}"
            run_id = new_run_id()
            if not ledger.claim(run_id=run_id, job=job,
                                idempotency_key=key, input_sha256=input_sha):
                # Same job + name + hash already staged: archive the
                # duplicate SMB copy, do not stage it a second time.
                dest = archive_dir / job / src.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                atomic_move(src, dest)
                counts["DUPLICATE"] = counts.get("DUPLICATE", 0) + 1
                log.warning("outbound %s duplicate, archived without re-staging", src.name)
                continue
            manifest = {
                "version": MANIFEST_VERSION,
                "run_id": run_id,
                "job": job,
                "direction": "outbound",
                "created_at": utcnow_iso(),
                "source": (f"smb:{outbound_cfg.get('requests_dir', 'upload-requests')}"
                           f"/{job}/{src.name}"),
                "idempotency_key": key,
                "input_name": src.name,
                "input_sha256": input_sha,
                "files": [file_entry(src, "output")],
            }
            out_dir = ftp_root / "outgoing" / job
            out_dir.mkdir(parents=True, exist_ok=True)
            tmp_manifest = out_dir / f"{src.stem}.manifest.json"
            # Copy data first, manifest last, both via .part + rename.
            atomic_publish(src, out_dir / src.name)
            write_manifest(tmp_manifest, manifest)
            # Verify the outgoing copy before touching the SMB original.
            problems = verify_files(out_dir, manifest)
            if problems:
                ledger.set_state(run_id, "FAILED", reason="; ".join(problems))
                counts["FAILED"] = counts.get("FAILED", 0) + 1
                log.error("outbound %s failed verification: %s", src.name, problems)
                continue
            ledger.set_state(run_id, "OUTBOUND_READY", outgoing=str(out_dir / src.name))
            dest = archive_dir / job / src.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            atomic_move(src, dest)
            counts["OUTBOUND_READY"] = counts.get("OUTBOUND_READY", 0) + 1
            log.info("outbound %s staged in %s (run %s)", src.name, out_dir, run_id)
    return counts


def scan_once(cfg: dict) -> dict[str, int]:
    ftp_root = Path(cfg["ftp_root"])
    ledger = Ledger(Path(cfg["ledger_db"]))
    counts: dict[str, int] = {}
    try:
        incoming = ftp_root / "incoming"
        for manifest_path in sorted(incoming.glob("*/*.manifest.json")):
            # Ignore a manifest whose data file has not landed yet:
            # the agent uploads data first, manifest last, so a lone
            # manifest means the upload is still in flight.
            try:
                manifest = read_manifest(manifest_path)
                data_name = next(f["name"] for f in manifest["files"]
                                 if f["role"] == "output")
            except (KeyError, ValueError, OSError, json.JSONDecodeError):
                continue
            if not (manifest_path.parent / data_name).exists():
                continue
            try:
                state = process_manifest(cfg, manifest_path, ledger)
            except (ValueError, OSError, Exception) as e:
                log.error("run at %s failed unexpectedly: %s", manifest_path, e)
                state = "FAILED"
            counts[state] = counts.get(state, 0) + 1
        # Reverse flow: SMB upload-requests -> FTP outgoing.
        for state, n in scan_outbound_once(cfg, ledger).items():
            counts[state] = counts.get(state, 0) + n
    finally:
        ledger.close()
    return counts


def reconcile(cfg: dict) -> list[str]:
    """Daily check: every PUBLISHED run must have its file on the share.

    Returns a list of problems (empty = share matches the ledger).
    """
    ledger = Ledger(Path(cfg["ledger_db"]))
    problems: list[str] = []
    try:
        for run in ledger.list_by_state("PUBLISHED"):
            detail = ledger.get(run["run_id"])
            published = detail["detail"].get("published") if detail else None
            if not published or not Path(published).exists():
                problems.append(f"{run['run_id']}: published file missing: {published}")
    finally:
        ledger.close()
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FileFlow Watcher (FTP server)")
    ap.add_argument("--config", required=True)
    ap.add_argument("--loop", type=int, default=0,
                    help="poll every N seconds; 0 = scan once")
    ap.add_argument("--reconcile", action="store_true",
                    help="check ledger PUBLISHED runs against the share, then exit")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)
    setup_logging(args.log_level)
    cfg = load_config(args.config)
    if args.reconcile:
        problems = reconcile(cfg)
        if problems:
            for p in problems:
                log.error("reconcile: %s", p)
            return 1
        log.info("reconcile: share matches ledger, no problems")
        return 0
    while True:
        counts = scan_once(cfg)
        if counts:
            log.info("scan: %s", counts)
        if not args.loop:
            return 0
        time.sleep(args.loop)


if __name__ == "__main__":
    raise SystemExit(main())
