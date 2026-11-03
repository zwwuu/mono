# FileFlow changelog

FileFlow is in active development. Breaking changes are allowed between
versions; this file records them so an upgrade never surprises.

## 0.14.0

- **Dashboard server moved from stdlib `http.server` to FastAPI +
  uvicorn** (`web/server.py` rewritten around `create_app(engine, db,
  scheduler, registry)`; `serve()` now runs `uvicorn.run`, same
  signature, so `app.py`/`cli.py` are untouched). Every route path,
  JSON shape, and status code is preserved, so the dashboard JS is
  unchanged — including the SSE log stream, now a FastAPI
  `StreamingResponse` over the same backlog-then-live generator
  (backlog from the DB, live entries via `LogBus`, `: ping` heartbeat
  every second, dedup by last log id). The sync generator runs in
  Starlette's threadpool, so the 1s sleep never blocks the event loop.
  Two deliberate improvements, not regressions: non-integer run/schedule
  ids now return a clean 400 instead of a dropped connection, and
  unknown paths keep the `{"error": "not found"}` 404 contract via a
  FastAPI exception handler. Bonus: interactive API docs at `/docs`.
  New runtime dependencies `fastapi>=0.110` and `uvicorn>=0.30`
  (uvicorn access logs off — the loguru console stays quiet).
  New test `tests/test_server_api.py` exercises the whole API over a
  real uvicorn thread with stdlib urllib only.

## 0.13.0

- **Dashboard restyled with Bootstrap 5.3.3.** The custom dark CSS is
  replaced by Bootstrap classes (tables, buttons, forms, badges,
  layout), with `data-bs-theme="dark"` so the dark look stays.
  Bootstrap is vendored as `web/static/bootstrap.min.css` (no CDN
  dependency — the dashboard works offline), served by a new
  `GET /static/bootstrap.min.css` route (exact-path allowlist, so no
  path traversal). All element IDs and dashboard JS logic are
  unchanged — only classes changed — and the leftover custom CSS is
  just run-status colors, the monospace log, and the two-pane layout.
  Verified in a real browser: 17 task rows render, primary buttons
  compute to Bootstrap blue (#0d6efd), zero page errors.

## 0.12.3

- **pytest-playwright back to the `dev` extra**, per zw's
  clarification: the runtime step drives a browser (login, click,
  download) through playwright's own API — which is exactly what
  `browser_fetch.py` already does — not through pytest fixtures, so
  pytest-playwright is test-only after all. This reverses the 0.12.2
  move, which rested on the ambiguous "runtime step" answer.

## 0.12.2

- **pytest-playwright back to runtime dependencies**, per zw: it is
  used by a runtime FileFlow step (so it is not a dev-only package).
  `pytest` itself stays in the `dev` extra — it arrives transitively
  with pytest-playwright at runtime anyway. Note: no step in the
  current tree imports pytest-playwright yet, so this declares it for
  the step being built.

## 0.12.1

- **Dependency correction.** `requests` is restored to runtime
  dependencies: zw's added libraries are declared for later use and
  must not be removed on unused-grounds alone (correction of the 0.12.0
  removal). `pytest`/`pytest-playwright` stay in the `dev` extra —
  nothing in `src/` imports them; see the 0.12.0 entry for why they
  were listed as runtime deps in the pushed pyproject.

## 0.12.0

- **Loguru logging: console + date-rotated files.** New
  `infra/logging_setup.py` configures two sinks at startup: stderr and
  `<data_dir>/logs/fileflow-YYYY-MM-DD.log`, rotated at midnight and
  kept for `app.log_retention_days` (default 30). Level comes from
  `app.log_level` (default `INFO`); both keys are in
  `config.example.json` and the shipped `config.json`. Wired in:
  `App` configures logging first and logs startup; `Engine.log`
  mirrors every run/step log to the file and console (the DB row stays
  the dashboard's source of truth); scheduler fires log at INFO and
  skips at DEBUG; the dashboard's HTTP access log goes to DEBUG
  instead of being dropped; CLI startup lines use the logger instead
  of `print`. A file-sink failure (e.g. unwritable data dir) falls
  back to console-only instead of crashing startup.
- **Dependency review (prompted by the pushed pyproject).** The pushed
  list dropped `playwright` — which would have broken every browser
  source (it is imported at module level) — and put test-only
  `pytest`/`pytest-playwright` in runtime dependencies, while the
  `smb` extra vanished (breaking SMB `smbprotocol` mode) and
  `requests` was listed but is imported nowhere. Fixed list:
  `loguru`, `paramiko` (kept: planned SFTP mode, not yet used by
  transfers.py), `playwright`, `pydantic`, `sqlalchemy` as runtime;
  `smb` extra restored; `pytest`/`pytest-playwright` moved to a `dev`
  extra. Note: `pytest-playwright` pulls in `playwright` as a
  dependency, which is likely why the missing `playwright` entry went
  unnoticed in a test install. Pydantic/SQLAlchemy floors adopted from
  the pushed list (2.14.1 / 2.1.6).

## 0.11.0

- **Pydantic task validation.** The hand-written checks in
  `core/tasks.py` (`validate_step`, `_validate_notify_params`, and the
  structural half of `validate_task`) are replaced by Pydantic v2
  models: discriminated unions for step types and source types,
  `RunLimitModel`, `CopyTargetModel`, `WhenModel`, and `TaskModel`
  (with `extra="allow"`, so unrecognized fields still pass through
  exactly as before). The models enforce the same rules — same
  rejections, now with field paths in the error
  (`steps.1.copy.to.type: Input should be 'ftp', 'local' or 'smb'`).
  Two deliberate tightenings: shorthand fields are now type-checked
  even when explicit `steps` are present (previously ignored), and
  `run_limit.max` is a strict int (the string `"3"` no longer
  coerces). Semantic checks Pydantic cannot express — rename pattern
  rendering, browser action shapes, `approval_after` points — stay in
  `validate_task`. The registry keeps plain dicts: models validate at
  the boundary (file load, dashboard/API saves) and do not replace the
  representation the engine consumes. A new test asserts every shipped
  task file (35 tasks) still validates. Config loading is deliberately
  untouched: it is default-driven with no validation to replace.

## 0.10.0

- **SQLAlchemy persistence.** `infra/db.py` no longer uses raw
  `sqlite3`: the same five tables (`runs`, `step_runs`, `logs`,
  `audit_logs`, `schedules`) are now SQLAlchemy ORM models, with a
  short-lived session per method and the same lock discipline as
  before. The DB class API and every returned dict shape are
  unchanged, so the engine, scheduler, dashboard, and tests needed no
  changes — and database files from earlier versions open and work
  untouched (verified in both directions).
- **Playwright is now a core dependency.** `playwright>=1.40` moved
  from the `browser` extra into `dependencies`; the extra is removed.
  `browser_fetch.py` imports Playwright at module level instead of
  lazily, and a missing Chromium binary fails with a clear
  "`playwright install chromium`" message. The binary itself is still
  installed separately. The old "missing Playwright package" test is
  replaced by a missing-binary test.
- **Caught during this version:** a rewrite of `run_browser_source`
  accidentally dedented the action loop outside the
  `sync_playwright()` context, so every browser action ran against a
  stopped Playwright ("Event loop is closed!"). Fixed by rewriting the
  function with the loop inside the context; all browser tests pass.

## 0.9.0

- **Per-task run limits.** A task can carry
  `"run_limit": {"max": N, "period": "day"|"week"|"month"|"total"}`
  (period defaults to `day`). Runs created in the current local
  calendar period count, whatever their outcome. Past the limit, starts
  are refused (API: 409 with `limit_exceeded: true`) and scheduled
  fires are skipped and advanced. Automatic runs never override a
  limit.
- **Dashboard override.** When a start is refused for the limit, the
  dashboard asks "Run anyway?" and re-sends with an override flag (also
  accepted on schedule run-now). The override is audit-logged, and the
  run still counts toward the total. Already-running refusals are not
  overridable. Task rows show live usage (`limit 3/3 per day`).
- **New live task** `scenario-run-limit` (max 3/day) in both
  `tasks.scenarios.json` (now 16) and the shipped `tasks.json` (now 17
  tasks). Scheduler `fire()`/`run_now()` now return
  `(run_id, note, limit_exceeded)`.

## 0.8.0

- **One active run per task.** Starting a task that already has a run
  in PENDING, RUNNING, AWAITING_APPROVAL, or STUCK is refused
  (`AlreadyRunningError`; API: 409 naming the active run; the dashboard
  greys out that task's Run button). A schedule firing for a busy task
  is skipped with a note instead of failing. Different tasks still run
  concurrently. The check-and-create is atomic, so two simultaneous
  starts can't both slip through.
- **Abandon a stuck run.** `POST /api/runs/<id>/fail` (dashboard:
  Abandon) marks a STUCK run FAILED, freeing its task. Without this, a
  permanently wedged task could never run again under the new rule.
- **Shipped `tasks.json` now holds all 16 live tasks**: the default task
  plus the fifteen scenario tasks (zw: the scenarios are the live task
  set, not just examples). `tasks.scenarios.json` remains as the
  reference gallery.

## 0.7.0 — cleanup (breaking)

- **Runs, not workflows.** The execution record is now called a run
  everywhere: DB tables `runs` / `step_runs.run_id` / `logs.run_id` /
  `schedules.last_run_id`, API `/api/runs`, log-stream `?run_id=`,
  dashboard, and code (`create_run`, `get_run`, `run_id`). Old databases
  are not migrated — start fresh.
- **Rename token `{workflow_id}` is now `{run_id}`.**
- **Legacy config migration removed.** `tasks.json` is the only task
  source; a missing file means an empty task list, not a generated task.
  The `archive` and `rename` config sections are gone, as are the
  `ftp.inbox_dir` / `processed_dir` / `processed_pattern` /
  `process_timeout_seconds` keys. Explicit `wait_processed` steps without
  a timeout now fall back to `steps.timeout_seconds` (300s default).
- **Schedules require a task.** `POST /api/schedules` returns 400
  without a known `task_id`, and a schedule fires exactly its `task_id`
  — the old name-match / `default` / first-enabled fallbacks are gone.
  The dashboard schedule form requires picking a task.
- **Runs are created only via tasks.** `POST /api/workflows` (with its
  name-to-task fallback) is removed; use `POST /api/tasks/<id>/run`.
- **Schedule deletion fixed.** `DELETE /api/schedules/<id>` previously
  never ran (unreachable code); it works now.
- **Tests consolidated.** Shared scaffolding moved to
  `tests/helpers.py`; `test_sources.py` no longer imports from
  `test_tasks.py`.

## 0.6.0

- Extensible browser selectors: css/text/role/label/placeholder/testid/
  xpath/row/chain, plus a `register_selector` registry for custom kinds.

## 0.5.0

- New sources: ftp, smb, local-directory picks, and browser sources
  (Playwright: login, click, download).

## 0.4.0

- Multiple approvals per task (`approval_after`), free step sequences,
  conditional (`when`) and per-task/per-step templated notifications.

## 0.3.0

- Generic task engine: tasks in `tasks.json` (shorthand or explicit
  steps), per-task sources, renames and destinations.

## 0.2.0

- `src/` package layout, PEP 621 packaging, JSON configs, pytest suite.
