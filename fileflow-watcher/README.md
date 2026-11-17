# FileFlow Watcher

Runs on the FTP server, the only machine that can see the share
drive. One of two projects — the other is **fileflow-agent**,
which runs on the dedicated Ethernet PC. The two projects share
no code: each is self-contained and is deployed and updated on
its own machine. The only contract between them is the manifest
format (`common.py`: `MANIFEST_VERSION`) and the idempotency key
(`job|run_id|input-sha256`). Change either in one project,
change it in the other, and bump `MANIFEST_VERSION`.

```
website ──► Ethernet PC ──► FTP server (Watcher, this machine) ──► share drive
              (agent)         claim, verify, route,
                              run procedure, publish

share drive ──► FTP server (Watcher, this machine) ──► Ethernet PC ──► website
  upload-requests/  seal + stage in                (agent picks up
  <job>/<file>      outgoing/<job>/                 from outgoing/)
```

## What it does

It does **not** use FTP to read files: on the FTP server the
incoming directories are local paths (e.g. `/srv/ftp/incoming`).
It polls them, and for each manifest it finds:

1. **Claim** — atomic move of data file + manifest from
   `incoming/<job>/` to `processing/<run_id>/`. A second watcher
   or a restart can never claim the same file twice.
2. **Verify** — re-compute SHA-256 + size against the manifest.
   Mismatch → `quarantine/<run_id>/`, ledger QUARANTINED.
   Nothing is processed, nothing is published.
3. **Dedup** — ledger claim on the idempotency key. Already
   published → `duplicate/`, no re-run, no re-publish. The ledger
   stays PUBLISHED so reconcile still checks the share copy.
4. **Route** — job comes from the manifest / incoming directory.
   Unknown job → `failed/<run_id>/` + `reason.txt`. Never guess
   a procedure.
5. **Process** — run the job's registered procedure in the
   workdir. It writes a *new* output file; the input is never
   edited.
6. **Verify output** — exists, non-empty, parses if CSV.
7. **Publish** — copy output + final manifest to the share drive
   via `.part` + rename, then **read back** the share copy and
   compare SHA-256 + size. Only then is the run PUBLISHED.
8. **Archive** — the verified input + manifest move to
   `done/<run_id>/`.

**Reverse flow** (same loop, same program): it watches
`<share>/upload-requests/<job>/` for new files. A file is only
picked once stable (not a `.part`, mtime at least
`min_age_seconds` old). It seals it with a manifest, copies data
+ manifest to `<ftp>/outgoing/<job>/` (`.part` + rename,
manifest last), verifies the copy, and only then moves the SMB
original to `<share>/upload-archive/<job>/`. Enable per job with
`"outbound": true` plus the `outbound` section in the config.

**Reconciliation:** `fileflow-watcher --reconcile` checks every
PUBLISHED run still has its file on the share. Run it daily.

## Layout

```
src/fileflow_watcher/
  common.py      sha256, manifest, atomic .part+rename
  ledger.py      SQLite run ledger + idempotency keys + states
  procedures.py  procedure registry + built-ins
                 (rename-only, normalize-csv, uppercase-headers-csv)
  watcher.py     claim -> verify -> route -> run -> publish
                 + SMB upload-requests -> outgoing
config/
  watcher.example.json   copy to watcher.json and fill in
  procedures_local.py    add your business procedures here
tests/test_watcher.py    forward + reverse tests (no agent code)
```

## Install

```bash
pip install -e .
```

## Deploy (FTP server)

- SFTP daemon: OpenSSH (`sshd`, port 22) — preferred — or
  FileZilla Server / vsftpd for FTP/FTPS.
- Create the FTP user `fileflow` with write access to
  `/srv/ftp/incoming/` and read access to `/srv/ftp/processing/`.
- Mount the share drive at `/mnt/share`, visible **only** to this
  server (share ACL + firewall: SMB 445 from this IP only).

```bash
cp config/watcher.example.json config/watcher.json
# edit: ftp_root, share_root, jobs (procedure + share_dir per job),
#       outbound section for the reverse flow
fileflow-watcher --config config/watcher.json --loop 10
```

Run it as a service (`systemd` unit on Linux, NSSM / scheduled
task on Windows) so it restarts on boot.

## Adding a business procedure

```python
# config/procedures_local.py
from pathlib import Path
from fileflow_watcher.procedures import register_procedure

def reformat_invoices(input_path: Path, workdir: Path, ctx: dict) -> Path:
    out = workdir / f"INV-{input_path.name}"
    # ... your rules: parse, validate, reformat ...
    out.write_text(input_path.read_text(encoding="utf-8-sig"))
    return out

register_procedure("reformat-invoices", "1.0", reformat_invoices)
```

Then in `watcher.json`: `"jobs": {"invoices": {"procedure":
"reformat-invoices", "share_dir": "finance/invoices", ...}}`.
The watcher needs no code change. A procedure must write a *new*
output file and raise on bad input.

## Configuration (`config/watcher.json`)

`ftp_root`, `share_root`, `ledger_db`, `jobs` — one entry per job:
`procedure`, `share_dir`, `output_pattern`, `expect_csv`,
`outbound` (reverse flow on/off), and optionally
`procedures_module` (e.g. `procedures_local`). Plus `outbound`:
`requests_dir`, `archive_dir`, `min_age_seconds`.

## Folder layout at runtime

```
FTP server: /srv/ftp/incoming/<job>/     (agent uploads land here)
            /srv/ftp/processing/<run_id>/ (claimed work)
            /srv/ftp/done/<run_id>/       (verified input, archived)
            /srv/ftp/quarantine/<run_id>/ (hash/size mismatch)
            /srv/ftp/failed/<run_id>/     (procedure/publish failure + reason.txt)
            /srv/ftp/duplicate/<run_id>/  (already-published re-upload)
            /srv/ftp/outgoing/<job>/      (reverse flow: staged for agent pickup)
Share:      /mnt/share/<share_dir>/<output> + <output>.manifest.json
            /mnt/share/upload-requests/<job>/  (reverse flow: drop files here)
            /mnt/share/upload-archive/<job>/  (reverse flow: picked originals)
```

## Tests

```bash
pip install -e ".[dev]"
python -m pytest tests/ -q
```

4 tests, all with local folders (no agent, no server needed):
forward publish to the share, corrupt file quarantined, duplicate
not re-published, and reverse staging from the share to outgoing.
