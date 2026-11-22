# FileFlow Agent

Runs on the dedicated Ethernet PC. One of two projects — the other
is **fileflow-watcher**, which runs on the FTP server. The two
projects share one library, **fileflow-contract** (the manifest
format, run identity, and SHA-256 verification), installed once
per machine; everything else is each project's own. Change the
manifest format in the contract package and bump `MANIFEST_VERSION`.

```
website ──► Agent (this PC) ──► FTP server ──► share drive
              download, rename/     (watcher)
              format, seal, upload

share drive ──► FTP server ──► Agent (this PC) ──► website
  (watcher stages    pick up from
   into outgoing/)    outgoing/, upload
```

## What it does

**Download mode** (`--mode download`, the default): one run is one
batch. The source is one file (`website` URL, `local` path), every
stable file matching a glob in a drop folder (`local-dir` with
`dir` + `pattern`, skipping `.part`, still-being-written, and empty
files), every stable file in a remote directory (`remote-dir` over
FTP/SFTP, same selection rules), every stable file on a Windows
share (`smb`, via `smbprotocol`), or the downloads produced by
driving a portal in Chrome (`selenium`, an action script of
goto/fill/click/wait/wait_for/download — the same action language
as the monorepo FileFlow browser source, on Selenium instead of
Playwright):
1. Download the source file(s) to `<staging>/<run_id>` — `.part` +
   rename, never process a partial download.
2. Validate each one (non-empty; CSV must parse when `expect_csv`).
3. First rename/format each one, using the procedure named in the config.
4. Seal them with **one manifest**: SHA-256 + size of *every* output
   file in `files[]`. There is no manifest-level input hash — no
   single hash can name a set of files, and each file's own hash is
   what the watcher verifies. The idempotency key is `job|run_id`:
   the run_id is the run's identity.
5. Upload every output file, then the manifest *last*, via `.part` +
   rename, to `incoming/<job>/` on the FTP server. The manifest
   rename is the trigger that tells the watcher the batch is
   complete.
6. Verify the remote sizes, then delete the staging copy. The
   staging copy is kept until the upload is verified.

A run records itself UPLOADED with the list of files it uploaded
(job + name + hash each). The next run skips any file already in
that list — a re-run, a re-drop, or a loop pass over an unchanged
drop folder does not upload it again. A file whose content changed
has a new hash, so it is processed. If nothing is fresh, the run
is SKIPPED: no manifest, no upload, no new ledger row. Files from
a failed run are not recorded, so they retry.

**Pickup mode** (`--mode pickup`): the reverse flow. For each
batch manifest in `outgoing/<job>/` it waits until **every** file
the manifest lists is present, downloads them, re-verifies every
SHA-256, then uploads the whole batch to the website in **one
POST** carrying all the files (`file` parts + `run_id` + `job`;
a single-file batch also sends its `sha256`, and its reply is
checked for a returned `sha256`/`size` when the site provides
one). Only after a 2xx does it delete the outgoing files. A
batch is all or nothing: a missing, corrupt, or failed file
leaves the whole batch in `outgoing/` for the next pass.

`--mode both` runs download, then pickup, in one pass.

## Layout

```
src/fileflow_agent/
  domain/          pure business rules (stdlib only, no I/O)
    entities.py    RunRecord, run states (Manifest lives in fileflow-contract)
    ports.py       RunRepository / Transport / Source / WebsiteUploader
    integrity.py   rename rules (safe_name, render_name)
    procedures.py  procedure registry + built-ins
                   (rename-only, normalize-csv, uppercase-headers-csv)
  application/     use cases, written only against domain ports
    download.py    DownloadAndUploadUseCase (website/local -> FTP)
    pickup.py      PickupUseCase (FTP outgoing -> website)
  infrastructure/  the outside world, behind the ports
    filesystem.py  sha256, manifest I/O, atomic .part+rename
    persistence.py SQLAlchemy run ledger (RunRepository) + Alembic model
    transport.py   uploads/downloads: local (tests/pilot), ftp, sftp (paramiko);
                   remote_mtime (MDTM on FTP, stat on SFTP/local)
    sources.py     WebsiteSource, LocalSource, DirectorySource,
                   RemoteDirectorySource (remote-dir over a transport)
    smb_source.py  SmbSource (Windows share via smbprotocol)
    selenium_source.py  SeleniumSource (Chrome action script) + selector
                   registry (css/text/role/label/placeholder/testid/
                   xpath/row/chain, same shapes as the monorepo browser source)
    website.py     WebsiteUploader (multipart POST)
  adapters/
    cli.py         composition root: builds use cases from config
config/agent.example.json   copy to agent.json and fill in
config/agent-remote-dir.example.json   remote-dir source example
config/agent-smb-source.example.json   smb source example
config/agent-selenium-source.example.json   selenium source example
tests/test_agent.py         upload + pickup tests (no watcher code)
tests/test_sources.py       remote-dir / smb / selenium sources (fakes)
```

## Architecture

Clean architecture: `domain` <- `application` <- `infrastructure` <-
`adapters`. The dependency rule runs inward only -- the domain never
imports SQLAlchemy, FTP, or HTTP; a use case only talks to ports, so
the ledger can move to Postgres or a transport can be swapped without
touching a use case. `adapters/cli.py` is the composition root: the
only place that wires concrete infrastructure into the use cases.
The full layer diagram, module map, and extension guide are in the
architecture technical document (FileFlow Agent & Watcher -- Clean
Architecture, 2026-11-17).

## Install

```bash
pip install -e ../fileflow-contract   # the shared manifest contract, once
pip install -e .
# Optional, per source/transport used:
pip install -e ".[sftp]"      # SFTP transport, remote-dir over SFTP
pip install -e ".[selenium]"  # selenium source (Chrome must be installed too)
pip install -e ".[smb]"       # smb source
```

## Deploy (Ethernet PC)

Wired Ethernet only; Wi-Fi off; static IP or DHCP reservation.

```bash
cp config/agent.example.json config/agent.json
# edit: source.url, job, rename_pattern, ftp host/transport, pickup.upload_url
export FILEFLOW_FTP_PASSWORD='...'    # or FILEFLOW_SFTP_KEY_FILE
export FILEFLOW_UPLOAD_TOKEN='...'     # pickup mode only, if the site needs it
fileflow-agent --config config/agent.json --mode both --loop 300
```

Run it as a service (`systemd` unit on Linux, scheduled task on
Windows) so it restarts on boot. Secrets come from env vars,
never from the config file.

## Configuration (`config/agent.json`)

`job`, `source`, `expect_csv`, `agent_procedure`, `rename_pattern`
(tokens `{date} {time} {name} {ext} {job} {run_id}`), `staging_dir`,
`ledger_db`, `ftp` (`transport: local|ftp|sftp`, `host`, `port`,
`username`; for `local`, `root` is the simulated FTP root), and
`pickup` (`jobs`, `upload_url`, `field`, `auth_env`,
`timeout_seconds`).

`source` is one of:
- `website`: `url`, `timeout_seconds` (default 60).
- `local`: `path`.
- `local-dir`: `dir`, `pattern` (default `*`), `min_age_seconds`
  (default 2).
- `remote-dir`: `transport` (`ftp`/`sftp`/`local`), `host`, `port`,
  `username`, `password_env` (default `FILEFLOW_FTP_PASSWORD`),
  `key_file_env` (default `FILEFLOW_SFTP_KEY_FILE`), `remote_dir`,
  `pattern` (default `*`), `min_age_seconds` (default 5). For
  `local`, `root` is the remote root (pilot/test mode).
- `smb`: `server`, `share`, `path`, `username`, `password_env`
  (default `FILEFLOW_SMB_PASSWORD`), `pattern` (default `*`),
  `min_age_seconds` (default 5).
- `selenium`: `actions` (goto/fill/click/wait/wait_for/download;
  `fill` takes `value` or `secret`, an env var name), `headless`
  (default true), `timeout_seconds` (default 60), `chrome_binary`
  (optional). Selectors are a CSS string or an object with a
  `kind`: css/text/role/label/placeholder/testid/xpath/row/chain.
  See `config/agent-selenium-source.example.json`.


## Database migrations (Alembic)

The ledger database is managed with Alembic — that's the library
you were thinking of. The schema lives in `migrations/versions/`
(`001_initial.py` creates the `runs` table); the model in
`infrastructure/persistence.py` is the source for autogeneration.

```bash
# New database (run from the project root, with the ledger path set):
FILEFLOW_LEDGER_DB=/var/lib/fileflow-agent/agent.db alembic upgrade head

# Existing database that already has the runs table: mark it current
# instead of rebuilding it:
FILEFLOW_LEDGER_DB=/var/lib/fileflow-agent/agent.db alembic stamp head

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

17 tests, using the `local` transport and a local HTTP server
standing in for the website (`test_agent.py`: agent upload with
file + manifest, hash verifies; pickup upload to the website,
outgoing cleared; a corrupt staged file never uploaded; re-runs
skip already-processed files), plus the remote sources against
fakes (`test_sources.py`: remote-dir selection + end-to-end skip,
SMB selection, Selenium action flow / selectors / validation /
download timeout). The Selenium tests drive a fake in-memory DOM,
not a real Chrome — the real-browser path needs Chrome on the PC
and is not covered here.
