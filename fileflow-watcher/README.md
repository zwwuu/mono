# FileFlow Watcher

Runs on the FTP server, the only machine that can see the share
drive. One of two projects — the other is **fileflow-agent**,
which runs on the dedicated Ethernet PC. The two projects share
no code: each is self-contained and is deployed and updated on
its own machine. The only contract between them is the manifest
format (`domain/entities.py`: `MANIFEST_VERSION`) and the idempotency key
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
It polls them, and for each manifest it finds — one manifest is one
batch, however many files it lists:

1. **Claim** — atomic move of the data files + manifest from
   `incoming/<job>/` to `processing/<run_id>/`. A second watcher
   or a restart can never claim the same files twice.
2. **Verify** — re-compute SHA-256 + size of every file against the
   manifest. Mismatch → `quarantine/<run_id>/`, ledger QUARANTINED.
   Nothing is processed, nothing is published.
3. **Dedup** — ledger claim on the idempotency key. Already
   published → `duplicate/`, no re-run, no re-publish. The ledger
   stays PUBLISHED so reconcile still checks the share copies.
4. **Route** — job comes from the manifest / incoming directory.
   Unknown job → `failed/<run_id>/` + `reason.txt`. Never guess
   a procedure.
5. **Process** — run the job's registered procedure on **each** file
   in the batch, in the workdir. It writes *new* output files; the
   inputs are never edited. The run only completes when all files
   are processed.
6. **Verify output** — each output exists, non-empty, parses if CSV.
7. **Publish** — copy every output + the final manifest to the share
   drive via `.part` + rename, then **read back** each share copy
   and compare SHA-256 + size. Only then is the run PUBLISHED.
8. **Archive** — the verified inputs + manifest move to
   `done/<run_id>/`.

**Reverse flow** (same loop, same program): it watches
`<share>/upload-requests/<job>/` for new files. A file is only
picked once stable (not a `.part`, mtime at least
`min_age_seconds` old). One scan stages **one batch per job**:
every stable file currently in the job's requests dir is one
job, sealed under a single run_id with one manifest listing all
the files -- a job only completes when all its files are uploaded
to the website together. It copies the data files + manifest to
`<ftp>/outgoing/<job>/` (`.part` + rename, manifest last),
verifies the copies, and only then moves the SMB originals to
`<share>/upload-archive/<job>/`. A file already staged in an
earlier batch (same job + name + hash) is archived as a duplicate
instead of being staged again. Enable per job with
`"outbound": true` plus the `outbound` section in the config.

**Reconciliation:** `fileflow-watcher --reconcile` checks every
PUBLISHED run still has its file on the share. Run it daily.

## Layout

```
src/fileflow_watcher/
  domain/          pure business rules (stdlib only, no I/O)
    entities.py    Manifest, ManifestFile, RunRecord, run states
    ports.py       RunRepository / Procedure
    integrity.py   manifest verification, rename patterns, idempotency key
    procedures.py  procedure registry + built-ins
                   (rename-only, normalize-csv, uppercase-headers-csv)
  application/     use cases, written only against domain ports
    process.py     ProcessIncomingUseCase (claim -> verify -> process -> publish)
    outbound.py    StageOutboundUseCase (SMB upload-requests -> outgoing)
    scan.py        ScanUseCase (one polling pass: incoming + outbound)
    reconcile.py   ReconcileUseCase (PUBLISHED runs vs share)
  infrastructure/  the outside world, behind the ports
    filesystem.py  sha256, manifest I/O, atomic .part+rename
    persistence.py SQLAlchemy run ledger (RunRepository) + Alembic model
  adapters/
    cli.py         composition root: builds use cases from config
config/
  watcher.example.json   copy to watcher.json and fill in
  procedures_local.py    add your business procedures here
tests/test_watcher.py    forward + reverse tests (no agent code)
```

## Architecture

Clean architecture: `domain` <- `application` <- `infrastructure` <-
`adapters`. The dependency rule runs inward only -- the domain never
imports SQLAlchemy or the filesystem; a use case only talks to ports,
so the ledger can move to Postgres without touching a use case.
`adapters/cli.py` is the composition root: the only place that wires
concrete infrastructure into the use cases. The full layer diagram,
module map, and extension guide are in the architecture technical
document (FileFlow Agent & Watcher -- Clean Architecture, 2026-11-17).

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


## Database migrations (Alembic)

The ledger database is managed with Alembic — that's the library
you were thinking of. The schema lives in `migrations/versions/`
(`001_initial.py` creates the `runs` table); the model in
`infrastructure/persistence.py` is the source for autogeneration.

```bash
# New database (run from the project root, with the ledger path set):
FILEFLOW_LEDGER_DB=/var/lib/fileflow-watcher/watcher.db alembic upgrade head

# Existing database that already has the runs table: mark it current
# instead of rebuilding it:
FILEFLOW_LEDGER_DB=/var/lib/fileflow-watcher/watcher.db alembic stamp head

# A future schema change: edit the model in infrastructure/persistence.py, then:
alembic revision --autogenerate -m "add <what>"
alembic upgrade head
```

`FILEFLOW_DB_URL` (a full SQLAlchemy URL) works instead of
`FILEFLOW_LEDGER_DB` if you need it. The ledger's `create_all`
is still there as a first-run safety net, but schema changes go
through Alembic revisions — never by editing the table by hand.

## Tests

```bash
pip install -e ".[dev]"
python -m pytest tests/ -q
ruff check src tests
ruff format --check src tests
``````

4 tests, all with local folders (no agent, no server needed):
forward publish to the share, corrupt file quarantined, duplicate
not re-published, and reverse staging from the share to outgoing.
