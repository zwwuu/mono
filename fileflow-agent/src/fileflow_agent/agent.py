"""FileFlow Agent -- runs on the dedicated Ethernet PC.

Pipeline (no approval step):
  1. download the source file to ``<staging>/<run_id>.part``,
     then rename to its real name (never process a partial download);
  2. validate it (non-empty; CSV must parse if the job says so);
  3. first rename / format, using the procedure named in the config;
  4. seal it: a manifest.json with SHA-256 + size of the input AND
     the output, run_id, job, procedure + version;
  5. upload the output file, then the manifest *last*, via .part +
     rename, to ``incoming/<job>/`` on the FTP server;
  6. verify the remote sizes, then delete the staging copy.
     The staging copy is kept until the upload is verified -- the
     sender never deletes before the receiver has the bytes.

Source types: ``website`` (http/https URL) and ``local`` (a file path,
for a drop-folder or for tests). Browser-portal downloads (Playwright)
are a separate source type in full FileFlow; this agent keeps the
download step to plain HTTP + local files on purpose.

REVERSE FLOW (pickup mode): the FTP server's watcher stages files
from the SMB share into ``outgoing/<job>/``. The agent picks them up
from there and uploads them to the website:
  ``python -m fileflow_agent.agent --config config/agent.json --mode pickup``
  or ``--mode both`` to run download + pickup in one pass. Pickup
  verifies the manifest hash before uploading, checks the website's
  response (2xx, and a returned sha256/size when the site provides
  one), and only then deletes the outgoing files on the FTP server.
  A failed upload leaves the files in ``outgoing/`` for the next pass.

Run:  python -m fileflow_agent.agent --config config/agent.json
      python -m fileflow_agent.agent --config config/agent.json --loop 300
      python -m fileflow_agent.agent --config config/agent.json --mode pickup
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time
import urllib.request
import uuid
from pathlib import Path

from .common import (
    MANIFEST_VERSION, PART_SUFFIX, file_entry, log, new_run_id, read_manifest,
    render_name, setup_logging, sha256_file, utcnow_iso, verify_files,
    write_manifest,
)
from .ledger import Ledger
from .procedures import get_procedure
from .transport import build_transport


def load_config(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def download_source(source: dict, dest_dir: Path, run_id: str) -> Path:
    """Download to .part, then rename. Returns the completed local path."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    kind = source["type"]
    if kind == "website":
        url = source["url"]
        name = Path(urllib.request.urlparse(url).path).name or "download"
        final = dest_dir / name
        tmp = dest_dir / (name + PART_SUFFIX)
        req = urllib.request.Request(url, headers={"User-Agent": "FileFlow-Agent/1.0"})
        with urllib.request.urlopen(req, timeout=source.get("timeout_seconds", 60)) as r, \
                open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        tmp.replace(final)
        return final
    if kind == "local":
        src = Path(source["path"])
        final = dest_dir / src.name
        shutil.copyfile(src, final)
        return final
    raise ValueError(f"unknown source type {kind!r} (want website|local)")


def validate_input(path: Path, job_cfg: dict) -> None:
    if path.stat().st_size == 0:
        raise ValueError(f"downloaded file is empty: {path.name}")
    if job_cfg.get("expect_csv"):
        import csv
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        if not rows or not any(c.strip() for c in rows[0]):
            raise ValueError(f"not a parseable CSV: {path.name}")


def run_once(cfg: dict) -> dict:
    job = cfg["job"]
    run_id = new_run_id()
    staging = Path(cfg["staging_dir"]) / run_id
    staging.mkdir(parents=True, exist_ok=True)
    log.info("run %s job=%s starting", run_id, job)
    try:
        # 1-2. download + validate
        downloaded = download_source(cfg["source"], staging, run_id)
        input_sha = sha256_file(downloaded)
        validate_input(downloaded, cfg)
        log.info("run %s downloaded %s (%d bytes, sha256 %s...)",
                 run_id, downloaded.name, downloaded.stat().st_size, input_sha[:12])

        # 3. first rename / format (agent-side procedure, if configured)
        proc_name = cfg.get("agent_procedure", "rename-only")
        proc = get_procedure(proc_name)
        formatted = proc["fn"](downloaded, staging, {"job": job, "run_id": run_id})
        stem, ext = formatted.name.rsplit(".", 1) if "." in formatted.name else (formatted.name, "")
        ext = f".{ext}" if ext else ""
        out_name = render_name(cfg.get("rename_pattern", "{date}_{name}{ext}"),
                               name=stem, ext=ext, job=job, run_id=run_id)
        output = staging / out_name
        if formatted != output:
            formatted.replace(output)

        # 4. seal with a manifest (input + output hashes)
        manifest = {
            "version": MANIFEST_VERSION,
            "run_id": run_id,
            "job": job,
            "created_at": utcnow_iso(),
            "source_url": cfg["source"].get("url") or cfg["source"].get("path"),
            "agent_procedure": proc_name,
            "agent_procedure_version": proc["version"],
            "idempotency_key": f"{job}|{run_id}|{input_sha}",
            # Input hash is provenance / idempotency only: the input
            # file itself is NOT uploaded, so it must not appear in
            # "files" -- the watcher verifies only the files listed
            # there, i.e. the transferred output.
            "input_name": downloaded.name,
            "input_sha256": input_sha,
            "files": [
                file_entry(output, "output"),
            ],
        }
        manifest_path = staging / f"{output.stem}.manifest.json"
        write_manifest(manifest_path, manifest)

        # 5-6. upload output first, manifest last; verify; then clean up
        transport = build_transport(cfg["ftp"])
        try:
            remote_dir = f"incoming/{job}"
            transport.upload_with_manifest(output, manifest_path, remote_dir, out_name)
        finally:
            transport.close()
        Ledger(Path(cfg["ledger_db"])).claim(
            run_id=run_id, job=job,
            idempotency_key=manifest["idempotency_key"], input_sha256=input_sha)
        log.info("run %s uploaded %s to %s", run_id, out_name, remote_dir)
        output_sha = sha256_file(output)
        shutil.rmtree(staging)
        return {"run_id": run_id, "job": job, "output": out_name,
                "output_sha256": output_sha,
                "status": "UPLOADED"}
    except (ValueError, OSError, Exception) as e:
        # Keep the staging dir for inspection; never upload a bad file.
        log.error("run %s FAILED: %s (staging kept at %s)", run_id, e, staging)
        raise


def upload_to_website(file_path: Path, pickup_cfg: dict, *,
                      run_id: str, job: str) -> None:
    """POST a file to the website as multipart/form-data (stdlib only).

    Sends fields ``file``, ``run_id``, ``job``, ``sha256``. Auth, when
    configured, is a Bearer token from an env var named in
    ``pickup.auth_env`` -- never from the config file.
    A non-2xx response is a failure (file stays in outgoing/).
    When the site's JSON reply carries a ``sha256``/``checksum`` or a
    ``size``, it is compared with the uploaded file; a mismatch is a
    failure too. A site that returns no checksum is accepted on 2xx.
    """
    import mimetypes

    url = pickup_cfg["upload_url"]
    file_sha = sha256_file(file_path)
    boundary = uuid.uuid4().hex
    body = bytearray()

    def field(name: str, value: str) -> None:
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(f"{value}\r\n".encode())

    field("run_id", run_id)
    field("job", job)
    field("sha256", file_sha)
    body.extend(f"--{boundary}\r\n".encode())
    ctype = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    body.extend(
        f'Content-Disposition: form-data; name="{pickup_cfg.get("field", "file")}";'
        f' filename="{file_path.name}"\r\n'.encode())
    body.extend(f"Content-Type: {ctype}\r\n\r\n".encode())
    body.extend(file_path.read_bytes())
    body.extend(f"\r\n--{boundary}--\r\n".encode())

    headers = {"User-Agent": "FileFlow-Agent/1.0",
               "Content-Type": f"multipart/form-data; boundary={boundary}"}
    token_env = pickup_cfg.get("auth_env")
    if token_env and os.environ.get(token_env):
        headers["Authorization"] = f"Bearer {os.environ[token_env]}"
    req = urllib.request.Request(url, data=bytes(body), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=pickup_cfg.get("timeout_seconds", 60)) as r:
            resp_body = r.read()
            status = r.status
    except (OSError, Exception) as e:
        raise IOError(f"website upload failed for {file_path.name}: {e}") from e
    if not 200 <= status < 300:
        raise IOError(f"website upload rejected {file_path.name}: HTTP {status}")
    try:
        reply = json.loads(resp_body.decode("utf-8")) if resp_body else {}
    except (ValueError, UnicodeDecodeError):
        reply = {}
    if isinstance(reply, dict):
        remote_sha = reply.get("sha256") or reply.get("checksum")
        if remote_sha and str(remote_sha).lower() != file_sha:
            raise IOError(
                f"website reported sha256 {remote_sha} for {file_path.name}, "
                f"expected {file_sha}")
        if "size" in reply and int(reply["size"]) != file_path.stat().st_size:
            raise IOError(f"website reported size {reply['size']} for {file_path.name}")
    log.info("uploaded %s to website (run %s, HTTP %s)", file_path.name, run_id, status)


def pickup_once(cfg: dict) -> dict[str, int]:
    """Pick up staged files from FTP outgoing/<job>/ and upload them
    to the website. Returns counts, e.g. {"UPLOADED_TO_WEBSITE": 1}."""
    pickup_cfg = cfg.get("pickup")
    if not pickup_cfg:
        return {}
    jobs = pickup_cfg.get("jobs") or [cfg["job"]]
    ledger = Ledger(Path(cfg["ledger_db"]))
    transport = build_transport(cfg["ftp"])
    counts: dict[str, int] = {}
    try:
        for job in jobs:
            remote_dir = f"outgoing/{job}"
            for manifest_name in sorted(transport.list_names(remote_dir)):
                if not manifest_name.endswith(".manifest.json"):
                    continue
                staging = Path(cfg["staging_dir"]) / "pickup"
                staging.mkdir(parents=True, exist_ok=True)
                local_manifest = staging / manifest_name
                try:
                    transport.download(remote_dir, manifest_name, local_manifest)
                    manifest = read_manifest(local_manifest)
                except (ValueError, OSError, json.JSONDecodeError) as e:
                    log.error("pickup: bad manifest %s: %s", manifest_name, e)
                    counts["FAILED"] = counts.get("FAILED", 0) + 1
                    continue
                run_id = manifest["run_id"]
                data_name = next(f["name"] for f in manifest["files"]
                                 if f["role"] == "output")
                if data_name not in transport.list_names(remote_dir):
                    continue  # data file still in flight; try next pass
                local_file = staging / data_name
                try:
                    transport.download(remote_dir, data_name, local_file)
                except (OSError, Exception) as e:
                    log.error("pickup: download of %s failed: %s", data_name, e)
                    counts["FAILED"] = counts.get("FAILED", 0) + 1
                    continue
                problems = verify_files(staging, manifest)
                if problems:
                    # Hash mismatch: leave the outgoing files alone for
                    # the operator; never upload a corrupt file.
                    log.error("pickup: %s failed verification: %s", data_name, problems)
                    counts["FAILED"] = counts.get("FAILED", 0) + 1
                    continue
                is_new = ledger.claim(run_id=run_id, job=job,
                                      idempotency_key=manifest["idempotency_key"],
                                      input_sha256=manifest.get("input_sha256"))
                if not is_new and ledger.get(run_id)["state"] == "UPLOADED_TO_WEBSITE":
                    # Crash between website upload and outgoing cleanup:
                    # the site already has it, so just clear outgoing.
                    transport.delete(remote_dir, data_name)
                    transport.delete(remote_dir, manifest_name)
                    counts["DUPLICATE"] = counts.get("DUPLICATE", 0) + 1
                    log.warning("pickup: run %s already uploaded; cleared outgoing", run_id)
                    continue
                try:
                    upload_to_website(local_file, pickup_cfg, run_id=run_id, job=job)
                except (OSError, IOError, Exception) as e:
                    log.error("pickup: website upload failed for %s: %s", data_name, e)
                    counts["FAILED"] = counts.get("FAILED", 0) + 1
                    continue
                # Only now, after a verified upload, clear outgoing.
                transport.delete(remote_dir, data_name)
                transport.delete(remote_dir, manifest_name)
                ledger.set_state(run_id, "UPLOADED_TO_WEBSITE")
                local_file.unlink(missing_ok=True)
                local_manifest.unlink(missing_ok=True)
                counts["UPLOADED_TO_WEBSITE"] = counts.get("UPLOADED_TO_WEBSITE", 0) + 1
    finally:
        transport.close()
        ledger.close()
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FileFlow Agent (Ethernet PC)")
    ap.add_argument("--config", required=True)
    ap.add_argument("--mode", choices=["download", "pickup", "both"],
                    default="download",
                    help="download: website -> FTP (default); pickup: FTP outgoing"
                         " -> website; both: run download then pickup")
    ap.add_argument("--loop", type=int, default=0,
                    help="repeat every N seconds; 0 = run once")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)
    setup_logging(args.log_level)
    cfg = load_config(args.config)
    while True:
        failed = False
        try:
            if args.mode in ("download", "both"):
                run_once(cfg)
            if args.mode in ("pickup", "both"):
                counts = pickup_once(cfg)
                if counts:
                    log.info("pickup: %s", counts)
        except (ValueError, OSError, Exception) as e:
            log.error("agent run failed: %s", e)
            failed = True
        if not args.loop:
            return 1 if failed else 0
        time.sleep(args.loop)


if __name__ == "__main__":
    raise SystemExit(main())
