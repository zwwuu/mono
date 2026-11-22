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
FTP/SFTP, same selection rules), the named files in a `files`
list (`remote-files` over FTP/SFTP — each entry a `directory` +
`name` template rendered per run from the run date and job-level
values, every listed file required), every stable file on a Windows
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
   rename, to the destination directory — `destination.directory`
   when the config names one (a templated directory, e.g. a
   date-partitioned inbox for a direct drop with no watcher), else
   `incoming/<job>/` on the FTP server. The manifest rename is the
   trigger that tells the watcher the batch is complete.
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
    integrity.py   rename rules (safe_name, render_name) + template
                   rendering (template_context, render_template)
    procedures.py  procedure registry + built-ins
                   (rename-only, normalize-csv, uppercase-headers-csv)
  application/     use cases, written only against domain ports
    download.py    DownloadAndUploadUseCase (website/local -> FTP)
    pickup.py      PickupUseCase (FTP outgoing -> website)
  infrastructure/  the outside world, behind the ports
    config.py      config parser: JSON dict -> typed config, fails
                   loud at startup with every problem listed
    filesystem.py  sha256, manifest I/O, atomic .part+rename
    persistence.py SQLAlchemy run ledger (RunRepository) + Alembic model
    transport.py   uploads/downloads: local (tests/pilot), ftp, sftp (paramiko);
                   remote_mtime (MDTM on FTP, stat on SFTP/local)
    sources.py     WebsiteSource, LocalSource, DirectorySource,
                   RemoteDirectorySource (remote-dir over a transport),
                   RemoteFilesSource (remote-files: named templated files)
    smb_source.py  SmbSource (Windows share via smbprotocol)
    selenium_source.py  SeleniumSource (Chrome action script) + selector
                   registry (css/text/role/label/placeholder/testid/
                   xpath/row/chain, same shapes as the monorepo browser source)
    website.py     WebsiteUploader (multipart POST)
  adapters/
    cli.py         composition root: builds use cases from config
config/agent.example.json   copy to agent.json and fill in
config/agent-remote-dir.example.json   remote-dir source example
config/agent-remote-files.example.json   remote-files source + destination example
config/agent-smb-source.example.json   smb source example
config/agent-selenium-source.example.json   selenium source example
tests/test_agent.py         upload + pickup tests (no watcher code)
tests/test_sources.py       remote-dir / smb / selenium sources (fakes)
tests/test_config.py        config parser: valid shapes, every failure mode
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
# edit: source.url, job, rename_pattern, ftp host/transport, deliver.upload_url
export FILEFLOW_FTP_PASSWORD='...'    # or FILEFLOW_SFTP_KEY_FILE
export FILEFLOW_UPLOAD_TOKEN='...'     # pickup mode only, if the site needs it
fileflow-agent --config config/agent.json --mode both --loop 300
```

Run it as a service (`systemd` unit on Linux, scheduled task on
Windows) so it restarts on boot. Secrets come from env vars first
(`password_env`, default `FILEFLOW_FTP_PASSWORD`); a literal
`password` in the config is accepted as a fallback, but it sits in
a file, so prefer the env var.

## Configuration (`config/agent.json`)

The config file is parsed once, at startup, before any run starts
(`infrastructure/config.py`): the raw JSON becomes a typed config
(one dataclass per source type, a transport dataclass shared by
`destination` and the remote sources), and anything wrong raises
one `ConfigError` listing *every* problem found -- missing keys,
wrong types, unknown keys, unknown source/transport kinds, unknown
template tokens, out-of-range values -- instead of failing on the
first one, or halfway through a run with a bare `KeyError`. Check a
config without running it:

```bash
fileflow-agent --config config/agent.json --check-config   # exit 0 when valid, 2 with the problems listed
```

A config without a `source` section is valid (a pickup-only job);
running it in download mode fails loud at startup instead. Unknown
keys are rejected everywhere, so a typo like `destinaton` fails at
startup rather than silently doing nothing. The parser checks shape
only: it does not check that a named env var is set, and it does
not connect to anything.

`job`, `kind` (optional job-level value, usable as a `{kind}` token
in templates), `variables` (the job's own arbitrary template
tokens, name -> string), `date_format` / `time_format` (strftime,
defaults `%Y%m%d` / `%H%M%S`, driving the `{date}` / `{time}`
tokens), `source`, `expect_csv`, `procedure`,
`rename_pattern` (tokens `{date} {time} {name} {ext} {job}
{run_id}`, plus the job's `variables`), `staging_dir`, `ledger_db`,
`destination` (`transport: local|ftp|sftp`, `host`, `port`,
`username`, and an optional `directory` template for the upload
target), and `deliver` (`upload_url`, `field`, `auth_env`,
`timeout_seconds`).

The filename render is the job's business logic, not the
framework's: each job has its own datetime format and its own
arbitrary variables, and the parser validates templates against
the context built from that job's config -- so a typo still fails
at startup, but a future app's own tokens are never "unknown". A
variable name must be an identifier and must not collide with a
built-in token (`year`, `month`, `day`, `date`, `time`, `job`,
`kind`, `name`, `ext`, `run_id`).

`destination` is the upload transport everywhere, for both
download and pickup (one server per config, both directions), and
it is required -- a config without it fails loud. `destination.directory`
sets the upload target: when present the outputs + manifest go to
that templated directory (a direct drop, no watcher); when absent
they go to `incoming/<job>/`, which the watcher scans. Only set
`directory` for a direct drop -- a watcher-flow config must leave
it out, or the watcher never sees the uploads.

Templates (`remote-files` directory/name, `destination.directory`,
`rename_pattern`) render per run from the job's own context:
`{year}` `{month}` `{day}` (the run date's components, local
time), `{date}` `{time}` (in the job's `date_format` /
`time_format`), `{job}`, `{kind}` (from the job config's `kind`),
and the job's `variables`. An unknown token fails the run loud
instead of leaving `{token}` in a path.

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
- `remote-files`: `transport` (`ftp`/`sftp`/`local`), `host`, `port`,
  `username`, `password_env`, `key_file_env`, `min_age_seconds`
  (default 5), `files` (a non-empty list of `directory` + `name`
  templates). Every listed file is required: a missing, empty, or
  still-being-written file fails the run loud instead of uploading
  a partial batch. See `config/agent-remote-files.example.json`.
- `smb`: `server`, `share`, `path`, `username`, `password_env`
  (default `FILEFLOW_SMB_PASSWORD`), `pattern` (default `*`),
  `min_age_seconds` (default 5).
- `selenium`: `actions` (goto/fill/click/wait/wait_for/download;
  `fill` takes `value` or `secret_env`, an env var name), `headless`
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

60 tests, using the `local` transport and a local HTTP server
standing in for the website (`test_agent.py`: agent upload with
file + manifest, hash verifies; pickup upload to the website,
outgoing cleared; a corrupt staged file never uploaded; re-runs
skip already-processed files; a rename using the job's variables
and date format), plus the remote sources against
fakes (`test_sources.py`: remote-dir selection + end-to-end skip,
remote-files named-file fetching + direct-drop end-to-end,
template/password helpers, SMB selection, Selenium action flow /
selectors / validation / download timeout), plus the config parser
(`test_config.py`: valid shapes and defaults, every failure mode,
all five example configs parse, `--check-config` exit codes, the
job's variables and date/time formats). The
Selenium tests drive a fake in-memory DOM, not a real Chrome — the
real-browser path needs Chrome on the PC and is not covered here.
