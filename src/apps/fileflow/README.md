# FileFlow — generic file-processing task engine

A small Python project that runs file-processing **tasks** declared in
JSON, with a web dashboard showing live status and streaming logs. Each
task has its own source, rename schema, and copy destinations — add,
remove, or change tasks by editing `tasks.json` (or from the dashboard),
no code change needed. A human can approve a file mid-flow with one
click; if any step gets stuck, the operator is emailed and can retry the
step from the dashboard. Tasks run on demand or on a daily / weekly /
monthly schedule.

**Four required dependencies** — loguru (logging), SQLAlchemy
(persistence), Playwright (browser sources), and Pydantic (task
validation), all declared in `pyproject.toml`. Paramiko and requests
are declared for later use (planned SFTP mode and HTTP work).
pytest-playwright is test-only: runtime steps drive the browser through
playwright's own API, not pytest fixtures. Everything else
is Python standard library (`ftplib`, `smtplib`), plus FastAPI +
uvicorn for the dashboard server. The
only optional package is `smbprotocol`, and only
if you use SMB mode `smbprotocol`.

## Tasks — the generic engine

A task is one JSON object in `tasks.json`. The server reloads the file
whenever it changes, so edits take effect without a restart.

```json
{
  "id": "daily-reports",
  "name": "Daily Reports",
  "enabled": true,
  "source": {"type": "website", "url": "https://example.com/reports/daily.csv"},
  "approval": false,
  "rename": "{date}_{name}{ext}",
  "destinations": [
    {"type": "local", "dir": "reports/daily"},
    {"type": "smb", "dir": "reports"}
  ],
  "notify": true
}
```

Two ways to define the pipeline:

- **Shorthand** (above): `source`, `approval` (default true: one gate
  after checksum), `approval_after` (a list of step keys or types, e.g.
  `["fetch", "rename", "copy"]` — gates after each named step, so a task
  can require approval after download, after rename, and after every move
  to a destination, or no approval at all when `approval` is false),
  `rename` (default `{date}_{name}{ext}`), `destinations`, optional
  `processing` (`{"dir": "processed", "pattern": "{renamed_name}", ...}` —
  upload to the first FTP destination, wait for the processed file, then
  copy the processed file onward), `notify` (default true; or an object
  carrying notify-step params).
- **Explicit `steps`**: full control over order and parameters — steps
  need not follow one fixed sequence. A task can have no final
  destination (end after rename), no processing at all, processing
  before the copy, or processing before the rename. See
  `tasks.scenarios.json` for sixteen ready-made shapes.

### Step reference

| Step | Params | What it does |
|---|---|---|
| `fetch` | — (uses task `source`) | Fetch the source file into the run workdir. `source` is `{"type": "website", "url": ...}` or `{"type": "local", "path": ...}` (absolute, or relative to `data_dir`) |
| `checksum` | — | SHA-256 of the fetched file |
| `approval` | — | **Pause** until an operator clicks **Approve** (or Reject) on the dashboard. A task may have several approval steps at any positions; each one really waits |
| `rename` | `pattern` | Rename the current file per a template (below) |
| `copy` | `to: {"type": "ftp"\|"local"\|"smb", "dir": ...}` | Copy the current file to a destination. `local` dirs are absolute or relative to `data_dir`; `smb` dirs are subdirectories under the configured share |
| `wait_processed` | `dir`, `pattern`, `timeout_seconds`, `poll_seconds` | Poll an FTP directory until the processed file appears (timeout ⇒ STUCK) |
| `fetch_processed` | `dir` (optional) | Download the processed file; it becomes the current file for later copies |
| `notify` | `template`, `subject`, `recipients`, `when` | Send an email when reached — or skip it when its `when` condition is false |

The **current file** flows through the pipeline: `fetch` sets it, `rename`
replaces it, `fetch_processed` replaces it again, and `copy` steps copy
whatever is current at that point. So a task with processing copies the
processed file onward, while a task without it copies the renamed file.

Rename tokens: `{original} {name} {ext} {date} {time} {datetime}
{checksum} {checksum8} {run_id} {task}`. The rendered name must be a
plain filename (no `/` or `\`); unknown tokens are rejected when the task
is saved.

### Notifications — conditional, optional, templated per task and step

Notifications are optional everywhere: a task with no `notify` step sends
no completion mail, and stuck mail can be turned off globally with
`notify.on_stuck: false`.

- **Success mail** comes from `notify` steps, which can sit anywhere in
  the pipeline (a progress mail after fetch, a completion mail at the
  end). Each carries its own `template` and `subject` with tokens
  `{task} {task_name} {run_id} {step} {current_name} {downloaded_name}
  {downloaded_size} {sha256} {renamed_name} {processed_name}
  {processed_size} {processed_sha256}`. Unknown tokens are left literal,
  so a typo never breaks a run.
- **Conditional mail**: a notify step's `when` condition gates it, e.g.
  `{"field": "downloaded_size", "op": "gt", "value": 1000000}` or
  `{"field": "sha256", "op": "exists"}`. Ops: `exists eq ne gt lt ge le
  contains`; `current_size` is also available as a field. When false, the
  step succeeds with "skipped: condition not met" and sends nothing.
- **Failure mail** (a step goes STUCK) resolves its template by
  precedence: the failing step's own `failure_template` /
  `failure_subject`, then the task's, then the built-in default — so the
  failure mail for a copy step can differ from the one for a fetch step.
  A task can also set `recipients` to override the global notify list.

### Scenario gallery

`tasks.scenarios.json` holds sixteen copy-paste-ready tasks: approvals
after download / rename / every move; no approval at all; no final
destination; processing before rename and copy; processing then copy
onward; conditional notification; custom success and failure templates
(including a per-step one); local source file; progress mail
mid-pipeline; approval after every copy; **and the new source shapes** —
initial file picked from an FTP directory, from a share, from a local
directory, and a browser source that logs into a portal and downloads,
plus a browser source that picks a table row by title and downloads from
that row.
Validate any of them with the dashboard's add-task form — invalid tasks
are rejected with a reason.

### Sources — where the initial file comes from

| Source | Form | Notes |
|---|---|---|
| `website` | `{"type": "website", "url": ...}` | Plain HTTP download. In demo mode with no URL, the sample file in `demo_source/` is used |
| `local` file | `{"type": "local", "path": ...}` | One file; absolute, or relative to `data_dir` |
| `local` dir | `{"type": "local", "dir": ..., "pattern": "*.csv", "sort": "newest"}` | Pick one file from a directory by glob + sort (`newest`/`oldest`/`name`, default `newest`) |
| `ftp` | `{"type": "ftp", "dir": ..., "pattern": ..., "sort": ...}` | Pick one file from an FTP directory (demo mode reads `demo_ftp/`) |
| `smb` | `{"type": "smb", "dir": ..., "pattern": ..., "sort": ...}` | Pick one file from the share (demo mode reads `demo_smb/`) |
| `browser` | `{"type": "browser", "actions": [...]}` | Drive a real page: login, click, download (below) |

### Browser sources — login, click, download

Some portals have no direct download URL; the file only appears after
logging in and clicking through. A browser source declares those steps
as actions, played back with Playwright (Chromium):

```json
"source": {"type": "browser", "actions": [
  {"action": "goto", "url": "https://portal.example.com/login"},
  {"action": "fill", "selector": "#username", "value": "alice"},
  {"action": "fill", "selector": "#password", "secret": "PORTAL_PASSWORD"},
  {"action": "click", "selector": "button[type=submit]"},
  {"action": "wait_for", "selector": "a.download"},
  {"action": "download", "selector": "a.download"}
]}
```

Actions: `goto` (url), `fill` (selector + `value`, or `secret` naming an
environment variable — credentials never go in `tasks.json`, same rule as
the other FileFlow secrets), `click` (selector), `wait` (seconds),
`wait_for` (selector, optional `timeout_seconds`), `download` (selector,
optional `timeout_seconds` — clicks and saves the browser download; this
action must be present, and it produces the file). `headless: false` on
the source watches the run.

### Choosing elements — selectors beyond CSS

A `selector` in `fill` / `click` / `wait_for` / `download` is either a
plain CSS string (still works) or a selector object. Objects cover the
cases CSS can't:

| Selector | Matches |
|---|---|
| `{"kind": "css", "css": "a.download", "nth": 1}` | CSS, with optional `nth` (0-based) when several match |
| `{"kind": "text", "text": "Download report"}` | Visible text (substring; `exact: true` for exact) |
| `{"kind": "role", "role": "button", "name": "Sign in"}` | Accessible role + name |
| `{"kind": "label", "label": "Username"}` | Form field by its label |
| `{"kind": "placeholder", "placeholder": "Search…"}` | Input by placeholder |
| `{"kind": "testid", "testid": "download-btn"}` | `data-testid` attribute |
| `{"kind": "xpath", "xpath": "//a[contains(@href, 'csv')]"}` | XPath escape hatch |
| `{"kind": "row", ...}` | A table row by cell value (below) |
| `{"kind": "chain", "steps": [...]}` | Each step resolved inside the previous one |

Leaf kinds also accept a shorthand without `"kind"`: `{"text": "Download"}`
means `{"kind": "text", "text": "Download"}`.

**Table rows.** Find the row whose cell matches, then act on something
inside it — the "download the row titled X" case:

```json
{"kind": "row", "table": "#files", "column": "Title",
 "match": {"equals": "Q3 Report"}, "target": "a.download"}
```

`table` is CSS for the table (optional; first table by default).
`column` is a header name (matched case-insensitively) or a 0-based
index. `match` is a string (exact) or `{"equals" | "contains" | "regex":
...}`. `target` is any selector resolved inside the matched row
(omit it to select the row itself). `nth` picks among several matching
rows (default 0). A miss fails with the values actually seen, so you can
see why it didn't match.

**Extending it.** Kinds live in a registry (`SELECTOR_RESOLVERS` in
`infra/browser_fetch.py`): kind → resolver function taking a Playwright
Page-or-Locator and the spec. New kinds plug in without touching the
action runner:

```python
from fileflow.infra.browser_fetch import register_selector

def by_data_action(scope, spec):
    return scope.locator(f"[data-action='{spec['data_action']}']")

register_selector("data_action", by_data_action)
# now usable as {"kind": "data_action", "data_action": "export"}
```

Because resolvers receive a Page or a Locator, they compose with `chain`
and `row` targets automatically.

Playwright is a core dependency, so browser sources work out of the box
once the browser binary is installed:

```bash
pip install fileflow
playwright install chromium
```

Without the binary, a browser source fails with a clear message telling
you exactly that, instead of an obscure launch error.

### Managing tasks

- **Dashboard** — the **Tasks** section lists every task with Run /
  Enable / Disable / Delete, and an add-task form (paste a task JSON).
- **File** — edit `tasks.json` directly; the server picks up changes
  without a restart. The shipped `tasks.json` holds the default task
  plus all sixteen scenario tasks as live tasks; `tasks.scenarios.json`
  is the same set as a reference gallery, and `tasks.example.json`
  shows two tasks with different destinations and rename schemas.
- **API** — `GET /api/tasks`, `POST /api/tasks`, `GET/PUT/DELETE
  /api/tasks/<id>`, `POST /api/tasks/<id>/run`,
  `POST /api/tasks/<id>/enable|disable`.

Runs are kept as history even after their task is deleted; retrying a run
whose task changed to a different step list fails cleanly instead of
running the wrong steps.

**One active run per task.** A task cannot be started while it already
has a run in PENDING, RUNNING, AWAITING_APPROVAL, or STUCK — a second
start is refused (API: 409, naming the active run), and a schedule
firing for a busy task is skipped instead of crashing. Different tasks
run concurrently without restriction. A STUCK run counts as active, so
it blocks its task until it is retried or abandoned (`POST
/api/runs/<id>/fail` marks it FAILED and frees the task); a run waiting
at an approval gate is freed by rejecting it.

**Run limits.** A task can carry `"run_limit": {"max": 3, "period":
"day"}` (period: `day`, `week`, `month`, or `total`; defaults to
`day`). Runs created in the current local calendar period count toward
the limit — a started run consumes a slot even if it later fails or is
rejected. Past the limit, new starts are refused (API: 409 with
`limit_exceeded: true`) and scheduled fires are skipped; the dashboard
shows usage (`limit 3/3 per day`) on each task. Automatic runs never
override a limit — but a user can, from the dashboard: when a start is
refused for the limit, the dashboard asks "Run anyway?" and re-sends
with an override flag (the override is audit-logged, and the run still
counts toward the total).

## Quick start

The shipped `config.json` runs in **demo mode**: the "website", FTP server,
external processor and SMB share are simulated with local folders, so the
whole flow works out of the box.

```bash
pip install -e .          # or: pip install .
fileflow                  # open http://127.0.0.1:8080
# without installing: PYTHONPATH=src python -m fileflow
```

Pick the `default` task and click **▶ Run task**. It fetches, checksums,
then pauses — click **Approve**, watch it upload, wait for the simulated
processor (~8 s), download the result, archive, "distribute", and send the
completion email (written to `data/outbox/` as an `.eml` file when no SMTP
is set).

Run the automated tests (rename templates, task validation, registry
CRUD, multi-approval gates, conditional notify, failure templates,
multi-task end-to-end runs, scheduling, next-run math):

```bash
pip install -e ".[dev]"
pytest tests/             # or: python tests/test_tasks.py (no pytest needed)
ruff check --line-length 100 --select E,F,W src tests   # lint
ruff format --check --line-length 100 src tests         # format check
```

## Configuration (JSON)

Config files are plain JSON objects, one key per section. Resolution order:
`--config` flag, then `$FILEFLOW_CONFIG`, then `./config.json`.
`config.json` ships demo defaults; `config.example.json` is the documented
production template. Passwords never live in the config file — they come
from environment variables.

| Section | Key fields |
|---|---|
| `app` | `host`, `port`, `data_dir` (runtime state: db, work files, archive, outbox), `tasks_file` (default: `tasks.json` next to the config file) |
| `demo` | `enabled`, `source_file`, `process_delay_seconds` |
| `ftp` | `host`, `port`, `username` (password via `FILEFLOW_FTP_PASSWORD`), `poll_seconds`, `process_timeout_seconds`, `timeout_seconds` — used by `wait_processed` defaults and by FTP copy targets |
| `smb` | `mode` (`path` with `target_dir` pointing at an OS-mounted share, or `smbprotocol` with `server`/`share`/`path` — needs `pip install fileflow[smb]`, password via `FILEFLOW_SMB_PASSWORD`) |
| `smtp` | `host` (empty = write to `<data_dir>/outbox/`), `port`, `security` (`starttls`/`ssl`/`none`), `username` (password via `FILEFLOW_SMTP_PASSWORD`), `from` |
| `notify` | `recipients` (stuck alerts; completion mail goes to the same list), `on_stuck` |
| `steps` | `timeout_seconds` (per-step default; a step's own `timeout_seconds` wins) |
| `scheduler` | `tick_seconds` (how often due schedules are checked), `allow_overlap` (default false: a schedule never starts a new run while its previous run is still active) |

The old `source`, `rename`, and `archive` sections are only read for the
one-time migration described above.

## Scheduling — daily, weekly, monthly, or on demand

Tasks can be started three ways:

- **On demand** — pick a task in the dashboard header and click
  **▶ Run task**, or the **Run** button on the task itself.
- **On a schedule** — create schedules in the dashboard's **Schedules**
  section (or via the API): pick a task, a frequency — **daily**,
  **weekly** (choose the weekday), or **monthly** (choose the day of
  month) — and a time of day. Schedules are stored in the database, so
  they survive restarts. A schedule without a task falls back to a task
  with the same name, then the `default` task; a schedule whose task is
  disabled is skipped (noted in the audit log).
- **From your own automation** — `POST /api/tasks/<id>/run`, or
  `Engine.create_and_start(task_id)` from code.

Scheduler behavior:

- Times are **local server time**. The day of month clamps to the last
  day of shorter months (day 31 runs on Feb 28).
- If the server was down at the scheduled time, the schedule fires
  **once** when the server comes back (catch-up), not once per missed
  slot.
- A schedule won't start a new run while its previous run is still
  active (pending/running/awaiting approval/stuck); the skipped run is
  noted in the audit log. Set `scheduler.allow_overlap` to `true` to
  allow concurrent runs.
- A manual **Run now** does not move the schedule's next run time.

Schedule API:

```bash
# create: daily at 07:30 for a task (weekly needs "weekday": 0=Mon..6=Sun,
# monthly needs "day_of_month": 1-31)
curl -X POST localhost:8080/api/schedules -d '{"name":"daily-report","task_id":"daily-reports","frequency":"daily","time":"07:30"}'
curl localhost:8080/api/schedules                 # list (+ next/last run)
curl -X POST localhost:8080/api/schedules/1/run   # run now, on demand
curl -X POST localhost:8080/api/schedules/1/disable
curl -X DELETE localhost:8080/api/schedules/1
```

## Stuck handling

A step is marked **STUCK** when it raises an error or exceeds its timeout.
When that happens the engine:

1. Sets the run status to `STUCK` and records the reason.
2. Emails the configured recipients (`notify.recipients`, if
   `notify.on_stuck` is true).
3. Shows a **Retry step** button on the dashboard. Retry re-runs just the
   failed step and continues the run — earlier steps are not repeated.

## Project layout

```
pyproject.toml          packaging (PEP 621, src layout, `fileflow` console script)
config.json             active config (demo defaults)
config.example.json     documented production template
tasks.json              the live task list (default + all 16 scenario tasks)
tasks.example.json      two example tasks (different destinations + rename schemas)
tasks.scenarios.json    fifteen scenario tasks (multi-approval, no-destination, new sources, table-row download, …)
src/
  fileflow/
    __init__.py         version (single source, read by the build)
    __main__.py         `python -m fileflow`
    cli.py              `fileflow` entry point (arg parsing)
    app.py              wiring: config → DB → registry → engine → scheduler → server
    config.py           JSON config loading + env secrets
    core/
      engine.py         runner threads, approval gate, stuck detection, retry
      steps.py          parameterized steps (fetch/checksum/approval/rename/copy/...)
      tasks.py          task registry, validation (Pydantic models),
                        pipeline builder
    infra/
      db.py             SQLite via SQLAlchemy ORM: runs, step_runs, logs,
                        audit_logs, schedules (same tables as before, so
                        existing database files keep working)
      transfers.py      HTTP download, FTP (real/demo), SMB copy/download
      browser_fetch.py  browser sources (Playwright) + extensible selectors
      notify.py         SMTP email + outbox fallback
      demo.py           demo source file + simulated external processor
    scheduling/
      scheduler.py      daily/weekly/monthly schedules, catch-up, overlap guard
    web/
      server.py         dashboard HTTP API + SSE live-log streaming
      static/
        dashboard.html  dashboard UI (tasks, runs, approve/reject/retry, schedules, live log)
tests/
  test_tasks.py         task engine tests (pytest or standalone)
  test_smoke.py         end-to-end tests (pytest or standalone)
data/                   runtime state (db, work files, archive, outbox) — created on first run
```

## Notes & limitations

- **Logging** (loguru): every run/step log is mirrored from the DB to
  the console and to a date-rotated file,
  `data/logs/fileflow-YYYY-MM-DD.log` (new file at midnight, kept 30
  days). Configure with `app.log_level` (default `INFO`) and
  `app.log_retention_days` (default `30`) in config.json. Per-step
  detail also stays in the dashboard log stream.
- **Dashboard styling** is Bootstrap 5.3.3 (vendored, dark theme) —
  no CDN needed, works offline.
- The dashboard has **no built-in authentication** — bind it to localhost
  (default) or put it behind your own reverse proxy/VPN before exposing it.
- **State is a single SQLite file (`data/fileflow.db`)**, accessed through
  SQLAlchemy ORM; step history and an
  audit log of create/approve/reject/retry/stuck/task events are kept there.
- Steps run in threads with join-timeouts for stuck detection; the actual
  network operations also carry their own socket/poll timeouts, so a
  "stuck" step is detected promptly and safely in practice.
