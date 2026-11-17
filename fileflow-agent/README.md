# FileFlow Agent

Runs on the dedicated Ethernet PC. One of two projects — the other
is **fileflow-watcher**, which runs on the FTP server. The two
projects share no code: each is self-contained and is deployed and
updated on its own machine. The only contract between them is the
manifest format (`common.py`: `MANIFEST_VERSION`) and the
idempotency key (`job|run_id|input-sha256`). Change either in one
project, change it in the other, and bump `MANIFEST_VERSION`.

```
website ──► Agent (this PC) ──► FTP server ──► share drive
              download, rename/     (watcher)
              format, seal, upload

share drive ──► FTP server ──► Agent (this PC) ──► website
  (watcher stages    pick up from
   into outgoing/)    outgoing/, upload
```

## What it does

**Download mode** (`--mode download`, the default):
1. Download the source file to `<staging>/<run_id>.part`, then
   rename to its real name — never process a partial download.
2. Validate it (non-empty; CSV must parse when `expect_csv`).
3. First rename/format, using the procedure named in the config.
4. Seal it: `manifest.json` with SHA-256 + size of the input and
   the output, `run_id`, job, procedure + version.
5. Upload the output file, then the manifest *last*, via `.part` +
   rename, to `incoming/<job>/` on the FTP server. The manifest
   rename is the trigger that tells the watcher the transfer is
   complete.
6. Verify the remote sizes, then delete the staging copy. The
   staging copy is kept until the upload is verified.

**Pickup mode** (`--mode pickup`): the reverse flow. Downloads
data + manifest from `outgoing/<job>/` on the FTP server,
re-verifies the SHA-256, uploads to the website as
multipart/form-data (`file` + `run_id` + `job` + `sha256`),
checks the reply (2xx, and a returned `sha256`/`size` when the
site provides one), and only then deletes the outgoing files.
A failed or corrupt upload leaves the files in `outgoing/`.

`--mode both` runs download, then pickup, in one pass.

## Layout

```
src/fileflow_agent/
  common.py      sha256, manifest, atomic .part+rename, rename patterns
  ledger.py      SQLite run ledger + idempotency keys + states
  transport.py   uploads/downloads: local (tests/pilot), ftp, sftp (paramiko)
  procedures.py  procedure registry + built-ins
                 (rename-only, normalize-csv, uppercase-headers-csv)
  agent.py       download -> validate -> format -> seal -> upload
                 pickup:   outgoing -> verify -> upload to website
config/agent.example.json   copy to agent.json and fill in
tests/test_agent.py         upload + pickup tests (no watcher code)
```

## Install

```bash
pip install -e .
# SFTP transport only:
pip install -e ".[sftp]"
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

`job`, `source` (`website` URL or `local` path), `expect_csv`,
`agent_procedure`, `rename_pattern` (tokens `{date} {time} {name}
{ext} {job} {run_id}`), `staging_dir`, `ledger_db`, `ftp`
(`transport: local|ftp|sftp`, `host`, `port`, `username`; for
`local`, `root` is the simulated FTP root), and `pickup`
(`jobs`, `upload_url`, `field`, `auth_env`, `timeout_seconds`).

## Tests

```bash
pip install -e ".[dev]"
python -m pytest tests/ -q
```

3 tests, using the `local` transport and a local HTTP server
standing in for the website: agent upload (file + manifest,
hash verifies), pickup upload to the website (outgoing cleared),
and a corrupt staged file never uploaded.
