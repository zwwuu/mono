# sftp-uploader

Finds a local file by name pattern, uploads it to an SFTP server, verifies the
transfer, and emails you on success or failure. One dependency: `paramiko`.
Python 3.10+.

## Quick start

1. `pip install -r requirements.txt`
2. Edit `config.json` (SFTP host/credentials, file pattern, SMTP settings).
3. Put the file to upload in `./outbox/` (or set `file.local_dir`).
4. Run:

```bash
cd sftp-uploader
python -m sftp_uploader
```

Check what *would* be uploaded without touching the server:

```bash
python -m sftp_uploader --dry-run
python -m sftp_uploader --config /path/to/other-config.json
python -m sftp_uploader --verbose  # DEBUG logging regardless of config
```

## Testing (no production server needed)

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest
```

The suite starts a real SFTP server on localhost (in-process, see
`tests/stub_sftp.py`) and runs the actual `connect` / `upload` / `verify`
steps against it over the real SFTP protocol — password auth, key auth,
nested remote-dir creation, retry-after-failure, and failure attribution are
all covered. Nothing leaves your machine.

For a manual end-to-end against a disposable server, e.g. with Docker:

```bash
docker run -d -p 2222:22 -e SFTP_USERS=tester:secret:::upload atmoz/sftp
```

then point `sftp.host` / `port` / `username` / `password` / `remote_dir` at it.

## How it works

Four steps run in order, defined as classes in `sftp_uploader/steps.py`
(`FindFileStep`, `ConnectStep`, `UploadStep`, `VerifyStep`) and run by
`sftp_uploader.pipeline.Pipeline`:

| Step        | What it does |
|-------------|--------------|
| `find_file` | Expands `file.pattern` (strftime codes like `%Y%m%d` use today's date), globs `file.local_dir`, picks per `file.pick` (`newest` / `oldest` / `first`) |
| `connect`   | SSH handshake, then an SFTP session. Key auth if `sftp.key_file` is set (RSA/Ed25519/ECDSA), otherwise password auth |
| `upload`    | Creates `sftp.remote_dir` (with parents) if missing, uploads via `put` |
| `verify`    | Stats the remote file and compares size (set `verify: false` to skip) |

**Retry:** any step failure retries the whole run up to `retry.max_attempts`
times, with exponential backoff (`backoff_seconds`, doubling each attempt,
capped at `backoff_max_seconds`). Config errors fail immediately without retry.

**Failure reporting:** the log and the failure email both name the exact step
that failed, e.g. subject `[sftp-uploader] FAILED at step 'upload' after 3 attempt(s)`.
A success email names the file, size, destination, and duration. The failure
email is sent only after the final attempt, not on every retry.

**Exit codes** (for schedulers / wrapping scripts):

| Code | Meaning |
|------|---------|
| 0 | uploaded and verified |
| 1 | failed after all retries |
| 2 | config error (missing file, bad JSON, missing key) |
| 3 | no local file matched the pattern |

## config.json reference

- `sftp.key_file`: path to a private key (`~` is expanded) for key auth.
  Leave it `null` to use `sftp.password` instead. RSA, Ed25519 and ECDSA keys
  are all accepted; an encrypted key needs `sftp.key_passphrase`.
- `sftp.password` / `sftp.key_passphrase` / `email.password`: a plain string,
  or `{"env": "VAR_NAME"}` to read from an environment variable (recommended —
  keeps secrets out of the file).
- `sftp.host_key_policy`: `auto_add` (default, convenient) or `reject`
  (strict — the host key must already be in `~/.ssh/known_hosts`).
- `sftp.on_existing`: what to do when the remote file already exists —
  `overwrite` (default; re-uploads), `skip` (leaves it, reports SKIPPED), or
  `fail` (aborts the run with the step named in the failure email).
- Auth is fully config-driven: the SSH agent and `~/.ssh` key discovery are
  disabled, so the run behaves identically anywhere the config goes.
- `email.smtp_port: 465` uses SMTP_SSL; other ports use STARTTLS when
  `email.use_tls` is true. `email.enabled: false` turns notifications off
  (logs still record everything).
- `file.pattern` supports strftime codes *and* globs: `report-%Y%m%d.csv`,
  `sales-*.xlsx`, `backup-%Y-%m-%d.tar.gz` all work.

## Extending it

**New behavior via config only:** server, credentials, directories, file
pattern, retry policy, email recipients, log level/rotation — no code changes.

**New step via minimal code:** subclass `Step` in `sftp_uploader/steps.py`
and register it — nothing else changes:

```python
from common.logging import log  # static logger, usable anywhere
from sftp_uploader.context import RunContext
from sftp_uploader.steps import Step

class ChecksumStep(Step):
    name = "checksum"

    def run(self, ctx: RunContext) -> None:
        log.info("checksum: ...")
        # raise on failure; the pipeline attributes it to name='checksum'
```

```python
pipeline = Pipeline(config)
pipeline.steps.insert(2, ChecksumStep())  # run between connect and upload
pipeline.execute()
```

Retry, logging, step-failure attribution, and both notification emails pick
the new step up automatically. The logger is the shared static one:
`from common.logging import log` in any module gives you the configured
logger, no passing around.

## Logs

Console plus a rotating file (`logging.file`, default `sftp-uploader.log`,
1 MB × 3 backups). Every attempt logs each step start; failures log the step
name, error, and a traceback at DEBUG level. Passwords and passphrases are
never logged.

Log detail: INFO covers the run outline (pattern expansion, match count, the
picked file, connection target, remote-dir creation, transfer byte counts,
verify sizes, per-attempt outcome). DEBUG adds the expanded pattern, the
effective redacted config per attempt, per-step timings, existence checks,
and email subjects. `-v` / `--verbose` forces DEBUG regardless of
`logging.level` in the config.
