# FileFlow Watcher

Hands verified manifest batches from FTP 1 to FTP 2 for external
processing, then acts when the processor finishes each file. One
of two projects — the other is **fileflow-agent**, which runs on
the dedicated Ethernet PC and uploads the batches to FTP 1. The
two projects share one library, **fileflow-contract** (the
manifest format, run identity, and SHA-256 verification),
installed once per machine; everything else is each project's
own. Change the manifest format in the contract package and bump
`MANIFEST_VERSION`.

```
website ──► Ethernet PC ──► FTP 1 (agent uploads) ──► FTP 2 (processor)
              (agent)         (watcher copies)        success/ failed/

share drive ──► FTP 1 outgoing/ ──► Ethernet PC ──► website
  upload-requests/  (watcher stages)  (agent picks up)
  <job>/<file>
```

## What it does

Both FTPs are reached through transports (`local` / `ftp` /
`sftp`, same shape as the agent's destination), so the two FTPs
can be separate servers or two folders on one box — the config
decides. One scan pass does all three flows:

**Handoff** — for each manifest in FTP 1 `incoming/<job>/`
(one manifest is one batch, however many files it lists):

1. **Wait** — every fresh file the manifest lists must have
   landed (the agent uploads data first, manifest last).
2. **Verify** — re-compute SHA-256 + size of every fresh file
   against the manifest. Mismatch → `quarantine/<run_id>/` +
   `reason.txt`, ledger QUARANTINED. A corrupt file never
   reaches FTP 2.
3. **Dedup** — each file is checked against the ledger (job +
   name + sha256 in a SENT_TO_PROCESSING or COMPLETED run).
   Already recorded → moved straight to FTP 1 `done/<job>/`,
   no copy, no email. A changed file (new hash) copies as a
   new run; a FAILED outcome does not block a re-copy.
4. **Record** — the run is SENT_TO_PROCESSING with the file
   list (name + sha256 + size) in its detail, BEFORE any copy.
   A crash after this point is recoverable: the next scan
   resumes the copy instead of starting over.
5. **Copy** — the fresh files go to FTP 2 `processing/<job>/`
   via `.part` + rename, size- and hash-verified (each upload
   is downloaded back and its SHA-256 compared). Multi-file
   batches copy concurrently (4 workers, each with its own
   transport connection). The manifest is NOT copied: it is
   the agent↔watcher contract, not the processor's input. A
   file is skipped when FTP 2 already has the same name with
   the same size. A transient failure is retried up to 3 times
   with exponential backoff (1s, 2s); a hash mismatch fails
   the run immediately. Each handoff and each completion logs
   one summary line — files, total bytes, duration,
   destination — and the completion email carries the same
   summary.

A manifest for a job that is not in `jobs` is dead-lettered to
`failed/<run_id>/` + `reason.txt`, never guessed at. A run
already SENT_TO_PROCESSING is resumed by the handoff scan (any
file still missing on FTP 2 is re-copied; files the processor
already finished are left for the completion scan); a run left
RECEIVED by a crashed handoff is re-processed on the next scan.
A file that waits in SENT_TO_PROCESSING longer than
`stale_after_seconds` (default 86400 = 24h, 0 disables) gets
one alert email naming the file, job, run_id, and wait time;
with no `notify` section the alert is an error log line
instead. `--reconcile` still reports in-flight runs on demand.

**Completion** — the watcher polls FTP 2 `<job>/success/` and
`<job>/failed/` for files matching recorded SENT_TO_PROCESSING
runs:

1. **Match** — by name, then verified by SHA-256 read-back. A
   finished file with no recorded run is left alone with a
   warning, never deleted. A hash mismatch is a FAILED outcome.
2. **Archive** — the original on FTP 1 moves to `done/<job>/`
   or `failed/<job>/`, matching the outcome.
3. **Finish** — when every file of a run has an outcome, the
   run becomes COMPLETED (all success) or FAILED (any failed),
   the manifest moves to FTP 1 `done/<job>/` or
   `failed/<job>/`, and **one email** is sent for the batch
   (success summary, or failure naming the failed files).

**Reverse flow** (same loop, same program): it watches
`<share>/upload-requests/<job>/` for new files. A file is only
picked once stable (not a `.part`, mtime at least
`min_age_seconds` old). One scan stages **one batch per job**:
every stable file currently in the job's requests dir is one
job, sealed under a single run_id with one manifest listing all
the files -- a job only completes when all its files are uploaded
to the website together. It copies the data files + manifest to
FTP 1 `outgoing/<job>/` (`.part` + rename, manifest last),
verifies the copies, and only then moves the SMB originals to
`<share>/upload-archive/<job>/`. A file already staged in an
earlier batch (same job + name + hash) is archived as a duplicate
instead of being staged again.

**Reconciliation:** `fileflow-watcher --reconcile` lists the runs
still in flight (SENT_TO_PROCESSING) with per-file outcome
status and how long each file has waited since the handoff. A
report, not a repair: no state changes. Run it daily.

**File logging:** set `log_file` in the config and the watcher
also writes to that file (midnight rotation, 7 days kept);
console output is unchanged. Without it, console only.

## Layout

```
src/fileflow_watcher/
  domain/          pure business rules (stdlib only, no I/O)
    entities.py    RunRecord, run states
    ports.py       RunRepository / Transport
  application/     use cases, written only against domain ports
    handoff.py     HandoffUseCase (verify -> dedup -> copy) +
                   CompletionUseCase (match -> archive -> email)
    outbound.py    StageOutboundUseCase (SMB upload-requests -> outgoing)
    scan.py        ScanUseCase (one polling pass: handoff + completion + outbound)
    reconcile.py   ReconcileUseCase (in-flight report)
  infrastructure/  the outside world, behind the ports
    config.py      config parser: JSON dict -> typed config, fails
                   loud at startup with every problem listed
    filesystem.py  logging, atomic .part+rename helpers
    transport.py   local / FTP / SFTP transports (agent's design,
                   plus move + makedirs)
    notify.py      email notifications via SMTP
    persistence.py SQLAlchemy run ledger (RunRepository) + Alembic model
  adapters/
    cli.py         composition root: builds use cases from config
config/
  watcher.example.json   copy to watcher.json and fill in
tests/test_watcher.py    handoff + completion + reverse tests (no agent code)
tests/test_config.py     config parser: valid shapes, every failure mode
tests/test_logging.py    file logging: writes to log_file, none without it
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
pip install -e ../fileflow-contract   # the shared manifest contract, once
pip install -e .
pip install -e ".[sftp]"              # only if a transport is sftp (paramiko)
```

## Deploy

- FTP 1 and FTP 2: plain FTP (`vsftpd` / FileZilla Server) or
  SFTP (OpenSSH `sshd`, port 22) — SFTP preferred on a real
  network. The watcher needs write access to FTP 1
  `incoming/` + `done/` + `failed/` + `quarantine/` +
  `outgoing/`, and to FTP 2 `processing/` + `success/` +
  `failed/` (read + list).
- Mount the share drive at `/mnt/share`, visible **only** to this
  machine (share ACL + firewall: SMB 445 from this IP only).
  The share is only needed for the reverse flow.

```bash
cp config/watcher.example.json config/watcher.json
# edit: intake + processing transports, jobs, notify, outbound
fileflow-watcher --config config/watcher.json --loop 10
```

Run it as a service (`systemd` unit on Linux, NSSM / scheduled
task on Windows) so it restarts on boot.

## Configuration (`config/watcher.json`)

The config file is parsed once, at startup, before any scan starts
(`infrastructure/config.py`): the raw JSON becomes a typed config
(`WatcherConfig`), and anything wrong raises one `ConfigError`
listing *every* problem found -- missing keys, wrong types,
unknown keys, bad transport kinds, out-of-range values -- instead
of failing on the first one, or when a job's first manifest
arrives with a bare `KeyError`. Check a config without running it:

```bash
fileflow-watcher --config config/watcher.json --check-config   # exit 0 when valid, 2 with the problems listed
```

A broken config fails before the loop or the reconcile, never as
a per-run error.

- `intake` — FTP 1 (required): the transport (`transport:
  local|ftp|sftp`, `root` for local, `host`/`port`/`username`/
  `password_env` or literal `password` for ftp, plus
  `key_file_env`/`key_file` for sftp) and the per-job dirs
  `incoming_dir` / `done_dir` / `failed_dir` (defaults
  `incoming` / `done` / `failed`).
- `processing` — FTP 2 (required): the transport, same shape,
  and the per-job dirs `processing_dir` / `success_dir` /
  `failed_dir` (defaults `processing` / `success` / `failed`).
- `jobs` — a non-empty list of plain job names. A manifest for a
  job not in this list is dead-lettered, never guessed at.
- `ledger_db` (required), `share_root` (required when `outbound`
  is set — the reverse flow's share), `log_file` (optional:
  also log to this file, midnight rotation, 7 days kept).
- `notify` (optional): `smtp_host`, `smtp_port` (default 587;
  465 means implicit TLS, otherwise STARTTLS), `from`, `to`
  (one address, or a list), `username`, `password_env`
  (the SMTP password comes from that env var). When absent,
  completion messages are logged instead of emailed.
- `outbound` (optional): `requests_dir`, `archive_dir`,
  `min_age_seconds` — the reverse flow. Applies to every job
  in `jobs`.

Secrets come from env vars named in `*_env` keys
(`FILEFLOW_FTP_PASSWORD` / `FILEFLOW_SFTP_KEY_FILE` are the
defaults when no `*_env` is given); a literal `password` /
`key_file` in the config is the fallback.

## Folder layout at runtime

```
FTP 1: /srv/ftp1/incoming/<job>/     (agent uploads land here)
       /srv/ftp1/done/<job>/         (finished originals + manifests)
       /srv/ftp1/failed/<job>/       (failed originals + manifests)
       /srv/ftp1/quarantine/<run_id>/ (hash/size mismatch + reason.txt)
       /srv/ftp1/failed/<run_id>/    (unknown job + reason.txt)
       /srv/ftp1/outgoing/<job>/     (reverse flow: staged for agent pickup)
FTP 2: processing/<job>/             (handed-off files, awaiting the processor)
       success/<job>/                (processor: finished OK)
       failed/<job>/                 (processor: finished with errors)
Share: /mnt/share/upload-requests/<job>/  (reverse flow: drop files here)
       /mnt/share/upload-archive/<job>/  (reverse flow: picked originals)
```

## Removed in 2.6.0 (breaking, no backward compatibility)

- The procedure step is gone: no `procedures.py`, no
  `procedures_module`, no per-job procedure config. Processing
  is external now — the watcher hands files to FTP 2 and acts
  on the outcome.
- Publish-to-share is gone: no `share_root` forward flow, no
  `output_pattern`, no per-job `share_dir`. `share_root` stays
  only as the reverse flow's share root.
- `--reconcile` no longer checks the share: it reports runs
  still in flight (SENT_TO_PROCESSING) with per-file outcomes
  and wait times.
- Old forward-flow ledger states are gone: RECEIVED,
  SENT_TO_PROCESSING, COMPLETED, FAILED, QUARANTINED,
  DUPLICATE, OUTBOUND_READY, UPLOADED_TO_WEBSITE. States are
  plain strings in the ledger (no DB constraint), so no
  migration was needed; old rows in removed states simply
  sit there.

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
```

71 tests. All with local folders and fake transports/SMTP (no agent, no
server needed): handoff copies a verified batch to FTP 2 (and
never the manifest), an incomplete manifest is left alone, a
corrupt file is quarantined, a re-dropped file is archived as a
duplicate without re-copying, a changed file copies again, an
unknown job is dead-lettered, completion success/failure/hash
mismatch archive the original and send one email, an unknown
finished file is left alone, a mixed-outcome batch moves its
manifest by the batch outcome, reconcile reports in-flight runs,
the reverse flow stages from the share to outgoing (single file
and a multi-file batch in one job, duplicate archived), plus the
config parser (`test_config.py`: valid shapes and defaults,
every failure mode, the example config parses, `--check-config`
exit codes) and file logging (`test_logging.py`). Transfer
quality (`test_transfer_quality.py`): hash/size mismatch after
upload raises and no manifest follows, the handoff records
before copying, a resuming run re-copies only missing files and
never a finished one, transient failures retry with backoff and
validation errors never retry, a stuck file alerts once (and
retries after a failed send), multi-file uploads run
concurrently, and the per-run summary lands in the log and the
completion email.
