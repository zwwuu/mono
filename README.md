# Python monorepo template

Several apps, one repo, shared code, executables out the other end.

```
py-monorepo/
├── .github/workflows/ci.yml  # CI: lint, format check, tests, build-all
├── pyproject.toml          # shared libraries + dev tooling (apps have their own)
├── src/
│   └── common/             # shared libraries (stdlib-only)
│       ├── logging/        # shared logger (stdlib logging)
│       ├── mail/           # shared SMTP mail client
│       └── utils/          # shared helpers: paths, config, json, retry
├── apps/
│   ├── app-one/            # app 1: report sender
│   │   ├── pyproject.toml        # this app's own project file
│   │   ├── build.py              # builds app-one
│   │   └── src/app_one/          # the app package
│   │       ├── meta.py           # APP_NAME + __version__ (constants only)
│   │       ├── config/config.json  # the app's own config (bundled + overridable)
│   │       └── assets/           # the app's own bundled files
│   ├── app-two/            # app 2: file lister (same shape)
│   ├── app-three/          # app 3: URL health check (same shape)
│   ├── app-four/           # app 4: duplicate-file finder (same shape)
│   └── app-five/           # app 5: note indexer (multi-module)
├── scripts/
│   ├── build_config.py     # DefaultBuildConfig: the shared build logic
│   └── build.py            # build all apps, or one by name
└── tests/                  # tests for the build scripts and their output
```

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"            # shared libraries + dev tooling
for d in apps/*/; do pip install -e "$d"; done   # every app

python -m app_one --dry-run        # or: app-one --dry-run
python -m app_two                  # or: app-two
python -m app_three --help         # or: app-three
python -m app_four --help          # or: app-four
python -m app_five --help          # or: app-five
pytest
```

## Sample apps

Each app owns its name and version in its `meta.py`
(`APP_NAME`, `__version__`, re-exported by `__init__.py`), prints
them with `--version`, and uses them for its logger name — bump
each app's version independently.
What each sample actually uses:

- **app-one** (report sender): logging + mail + utils — builds a
  report from a bundled template and emails it.
- **app-two** (file lister): logging + utils — lists files by glob
  and proves bundled-asset loading.
- **app-three** (URL health check): logging + mail + utils — checks
  URLs with retry, writes a JSON report, emails on failure, exits 1
  when anything is down.
- **app-four** (duplicate finder): logging + mail + utils — groups
  files by size then SHA-256, writes a JSON manifest, emails a
  summary. Read-only: it never deletes anything.
- **app-five** (note indexer): logging + utils — indexes a folder
  of Markdown notes into a JSON index, one module per pipeline
  stage (scanner, parser, index).

## How the pieces fit

**Shared libraries** are plain importable packages under `src/`:
`common.logging`, `common.mail`, `common.utils`. Every app imports
them like any other package (`from common.logging import setup_logging`).
They use only the standard library, so they never drag in new
dependencies. When a library needs a third-party package, add it to
`dependencies` in `pyproject.toml` — it becomes available to all apps.

**Apps** are self-contained project folders under `apps/`
(`apps/app-one`, ..., `apps/app-five`), each with its own
`pyproject.toml`, its own `build.py`, and `src/<package>/` holding
the package — a `meta.py` with its name and version, a `__main__.py`
exposing `main()`, plus its own `config/` and `assets/`. That gives
you three ways to run them: `python -m app_one` (once installed),
the `app-one` console command, and the PyInstaller exe. App code
uses absolute imports only
(`from app_one.report import ...`, never `from .report import ...`),
because PyInstaller runs `__main__.py` as a plain script, where
relative imports fail.

Copying one app folder out gives you a complete project file-wise —
pyproject, build script, package — but the app still imports
`common.*` from the monorepo, and its `build.py` still loads
`scripts/build_config.py` from the template root. A standalone copy
also needs `src/common/` (to run) and `scripts/build_config.py` (to
build) alongside it. No shared code is vendored into app folders,
so it stays defined once.

**Per-app config and data** live inside the app's own package:
`apps/<app>/src/<package>/config/` and
`apps/<app>/src/<package>/assets/` (package = app name with `-`
replaced by `_`). Onedir builds copy the app's
`DATA_FOLDERS` flat next to the exe (`config/`, `assets/`).
`common.utils.load_app_config` / `common.utils.app_file_path` find
them at runtime whether the app runs from source or frozen. The
same lookup works for pip-installed apps: when there is no source
checkout, the app's installed package dir (`site-packages/<package>/`)
is tried. Neither
name is special: they're just the defaults in
`DefaultBuildConfig.DATA_FOLDERS`, and an app can override that
attribute to use any folder names (at runtime,
`load_app_config(..., folder=...)` and `app_file_path()` take the
folder as a parameter). Because the data lives in the package, it is
declared as `package-data` in the app's own `pyproject.toml` so
wheels and sdists ship it too.

## Config precedence

`load_app_config("<app>", defaults={...})` merges, in order:

1. Built-in `defaults` passed by the app (always the base).
2. The first config file found:
   - a directory passed as `search_dirs` (tests, `--config-dir`),
   - when frozen: `config/` next to the exe (user-editable),
   - from source: `apps/<app>/src/<package>/config/config.json`,
   - installed: `site-packages/<package>/config/config.json`
     (pip-installed apps, when there is no source checkout).

The folder is a parameter, not a special name:
`load_app_config(..., folder="settings")` looks in
`settings/` instead — `"config"` is just the default.

Secrets (SMTP passwords etc.) never go in these files — the mail client
reads them from environment variables (`MAIL_HOST`, `MAIL_USERNAME`,
`MAIL_PASSWORD`, ...). See `common.mail.MailConfig.from_env`.

## Building executables

```bash
python scripts/build.py                 # build every app -> dist/
python scripts/build.py app-one         # build just app-one
python scripts/build.py all --clean     # wipe build cache first
```

The central script discovers apps by scanning `apps/*/build.py` and
runs the matching per-app build script(s), so adding an app needs no
edit here. You can also run an app's build script directly
(`python apps/app-one/build.py`) — it takes extra options
the central script doesn't forward, like `--name` and `--distpath`.

The build itself is object-oriented: `scripts/build_config.py` defines
`DefaultBuildConfig`, and each app's `build.py` is a tiny subclass
that sets `PACKAGE` — the app name comes from the package's own
`APP_NAME` in its `meta.py`, so the name is defined once. To
customize an app, override an attribute in its subclass — `CONSOLE = False` for a windowed GUI,
`HIDDEN_IMPORTS` for dynamic imports, `EXTRA_DATAS` for extra bundled
files, `ICON` for a Windows icon, `DATA_FOLDERS` for different folder
names — instead of editing the PyInstaller command line.

On Windows the output is `dist/app-one/app-one.exe`. There are no
`.spec` files: each app's `build.py` passes everything PyInstaller
needs on the command line (`--paths` pointing at the app's `src/`),
then copies the app's
data flat next to the exe (PyInstaller would force `--add-data`
into `_internal`, so the build doesn't use it).

Every build also bundles its output into a zip in `dist/`, named
`<app>-<version>-<timestamp>.zip` (e.g.
`app-one-0.1.0-20261026-230512.zip`) — the version comes from the
app's `meta.py`, the timestamp is local time. Unzipping a
bundle recreates the app folder. The unzipped build output stays
in `dist/` too; pass `--no-zip` to skip the bundle.
One quirk to know: PyInstaller still generates a spec internally even
in CLI mode — the build scripts point it at `build/` with
`--specpath`, so the repo stays spec-free.

PyInstaller notes that bite people:

- **Data files sit flat next to the exe.** Never open `config/...`
  or `assets/...` with a relative path — always go through
  `common.utils` (`load_app_config`, `app_file_path`).
- **Hidden imports.** If an app imports a module dynamically
  (`importlib`, plugins), PyInstaller can't see it — add it to
  `HIDDEN_IMPORTS` in the app's `build.py` subclass.
- **Antivirus false positives** on Windows are a known PyInstaller
  annoyance.

## Template files and folders

`config/` and `assets/` have fixed names, but template files don't:
one app's template might be `quarterly-report.xlsx`, another's a
`letters/` folder of `.docx` files. Those go through `EXTRA_DATAS`
in the app's `build.py` subclass — `(source, dest)` pairs, source
relative to the app folder, directories bundled recursively, dest
flat next to the exe:

```python
class AppTwoBuild(DefaultBuildConfig):
    PACKAGE = "app_two"
    EXTRA_DATAS = (("src/app_two/templates", "templates"),)
```

At runtime, locate them with `common.utils.app_file_path()`, which
takes any path inside the app's bundle instead of assuming `assets/`:

```python
path = app_file_path(APP_NAME, "templates", "monthly-summary.xlsx")
# from source: apps/app-two/src/app_two/templates/monthly-summary.xlsx
# frozen:      templates/monthly-summary.xlsx next to the exe
```

Template folders inside the app package also belong in
that app's `[tool.setuptools.package-data]` entry in its own
`pyproject.toml`, so wheels and sdists ship them — app-two's entry
covers `templates/**`. app-two demonstrates the whole flow: its
`templates/` folder holds `monthly-summary.xlsx` and
`letters/welcome-letter.docx`, and running it logs both resolved
paths.

Single files work the same way — app-one bundles one standalone txt
file as its report footer:

```python
class AppOneBuild(DefaultBuildConfig):
    EXTRA_DATAS = (("src/app_one/disclaimer.txt", "disclaimer.txt"),)
```

and reads it with `app_file_path(APP_NAME, "disclaimer.txt")`.
Single files belong in the app's `package-data` entry too
(app-one's covers `disclaimer.txt`).

## Adding a new app

1. `apps/<app>/` with its own `pyproject.toml` (name, version read
   from the package's `meta.py`, console command, package data —
   copy an existing app's and change the names), plus
   `src/<package>/` (package = app name with `-` replaced by `_`)
   with `meta.py` (`APP_NAME` and `__version__` — the app's own
   name and version, re-exported by `__init__.py`), `__main__.py`
   (`main()` +
   `if __name__ == "__main__": raise SystemExit(main())`),
   `config/config.json`, and `assets/` if it needs bundled files.
2. `apps/<app>/build.py` (copy an existing one, change
   `PACKAGE`). The central `scripts/build.py` picks it up
   automatically — no edit needed there. Override a
   `DefaultBuildConfig` attribute in the subclass if the app needs
   something special (windowed, hidden imports, extra data, icon,
   folder names). Then install it with
   `pip install -e apps/<app>`.

## Adding a new shared library

1. `src/common/<lib>/` with `__init__.py` re-exporting its public API.
2. Import it from any app. No `pyproject.toml` change needed unless it
   adds a dependency — then add it to `dependencies` in the root
   `pyproject.toml` (all apps) or document it as app-specific.

## Tests

```bash
pytest            # tests/ cover the build scripts and their output
ruff check src tests scripts apps
ruff format --check src tests scripts apps
```

Tests deliberately cover only the build scripts and their output,
using fixture apps in tmp dirs — never the shipped sample apps or
shared libraries — so they stay valid whatever apps the template
holds. The sample apps and libraries are examples to replace, not
code the suite pins down.
# mono
# mono
