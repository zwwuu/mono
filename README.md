# mono

Several apps, one repo, shared code, executables out the other end.

```
mono/
├── pyproject.toml          # the only build/dependency config
├── src/
│   ├── common/             # shared libraries (stdlib-only)
│   │   ├── logging/        # shared logger (stdlib logging)
│   │   ├── mail/           # shared SMTP mail client
│   │   └── utils/          # shared helpers: paths, config, json, retry
│   ├── app_one/            # app 1: report sender
│   │   ├── meta.py               # APP_NAME + __version__ (constants only)
│   │   ├── config/config.json    # the app's own config (bundled + overridable)
│   │   └── assets/               # the app's own bundled files
│   ├── app_two/            # app 2: file lister (same shape)
│   ├── app_three/          # app 3: URL health check (same shape)
│   ├── app_four/           # app 4: duplicate-file finder (same shape)
│   ├── app_five/           # app 5: note indexer (multi-module)
│   └── pdf_form_check/     # app 6: PDF form checker (exit codes)
├── scripts/
│   ├── build_config.py     # DefaultBuildConfig: the shared build logic
│   ├── build.py            # build all apps, or one by name
│   ├── app_one/build.py    # builds app-one (subclass of the default config)
│   ├── app_two/build.py    # same
│   ├── app_three/build.py  # same
│   ├── app_four/build.py   # same
│   ├── app_five/build.py   # same
│   └── pdf_form_check/build.py  # same
└── tests/                  # build tests + per-app test dirs (pdf_form_check/)
```

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

python -m app_one --dry-run        # or: app-one --dry-run
python -m app_two                  # or: app-two
python -m app_three --help         # or: app-three
python -m app_four --help          # or: app-four
python -m app_five --help          # or: app-five
python -m pdf_form_check --help    # or: pdf-form-check
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
- **pdf-form-check**: checks a PDF form's A1/A2 fields for "yes"
  and reports via exit code — see below.

## How the pieces fit

**Shared libraries** are plain importable packages under `src/`:
`common.logging`, `common.mail`, `common.utils`. Every app imports
them like any other package (`from common.logging import setup_logging`).
They use only the standard library, so they never drag in new
dependencies. When a library needs a third-party package, add it to
`dependencies` in `pyproject.toml` — it becomes available to all apps.

**Apps** are also packages under `src/` (`app_one`, `app_two`,
`app_three`, `app_four`, `app_five`, `pdf_form_check`), each with a
`meta.py` holding its name and version, a `__main__.py` exposing
`main()`, plus its own `config/` and `assets/` where it needs them.
That gives you three ways to run them: `python -m app_one`, the
`app-one` console command, and the PyInstaller exe. App code uses
absolute imports only
(`from app_one.report import ...`, never `from .report import ...`),
because PyInstaller runs `__main__.py` as a plain script, where
relative imports fail.

**Per-app config and data** live inside the app's own package:
`src/<package>/config/` and `src/<package>/assets/` (package = app
name with `-` replaced by `_`). Onedir builds copy the app's
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
declared as `package-data` in `pyproject.toml` so wheels and sdists
ship it too.

## Config precedence

`load_app_config("<app>", defaults={...})` merges, in order:

1. Built-in `defaults` passed by the app (always the base).
2. The first config file found:
   - a directory passed as `search_dirs` (tests, `--config-dir`),
   - when frozen: `config/` next to the exe (user-editable),
   - from source: `src/<package>/config/config.json`,
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

The central script discovers apps by scanning `scripts/*/build.py` and
runs the matching per-app build script(s), so adding an app needs no
edit here. You can also run an app's build script directly
(`python scripts/app_one/build.py`) — it takes extra options
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
needs on the command line (`--paths src/`), then copies the app's
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
relative to the repo root, directories bundled recursively, dest
flat next to the exe. At runtime, locate them with
`common.utils.app_file_path()`, which takes any path inside the
app's bundle instead of assuming `assets/`.

Single files work the same way — app-one bundles one standalone txt
file as its report footer:

```python
class AppOneBuild(DefaultBuildConfig):
    EXTRA_DATAS = (("src/app_one/disclaimer.txt", "disclaimer.txt"),)
```

and reads it with `app_file_path(APP_NAME, "disclaimer.txt")`.
Template files and folders inside the app package also belong in
that app's `[tool.setuptools.package-data]` entry in
`pyproject.toml`, so wheels and sdists ship them — app-one's entry
covers `disclaimer.txt`.

## pdf-form-check

Checks a PDF form's A1/A2 fields for "yes" and reports via exit
code — meant to be consumed by another program. The exit code is
the result; stdout carries one short line, errors go to stderr.

```bash
pdf-form-check form.pdf; echo $?
# MATCH: A1=yes   (exit 1)
```

| Code | Meaning |
| ---- | ------- |
| 0 | `NO_MATCH` — the form parsed, neither checked field is "yes" |
| 1 | `MATCH` — at least one checked field is "yes" |
| 2 | `USAGE` — bad command line |
| 3 | `INPUT_ERROR` — file missing, not a file, or unreadable |
| 4 | `MALFORMED` — not a PDF, corrupted/truncated, empty, or encrypted |
| 5 | `FIELDS_MISSING` — the PDF parsed, but a checked field is not in the form |

0 is a clean negative, not an error. The failure codes exist so the
caller can tell "no" apart from "couldn't check": a missing field or
a corrupt file must never silently read as "no". Only 1 means
"yes" — 3, 4, and 5 mean "couldn't check" and must not be treated
as a match.

What counts as "yes": case-insensitive, surrounding whitespace
ignored, a leading slash stripped (a checked checkbox reports as
`/Yes` in pypdf). Anything else — `yess`, `on`, `1`, empty — does
not count. Field names resolve forgivingly: exact, then last
segment (`Section.A1`), then case-insensitive.

Edge cases: fields that exist but are empty mean "no" (exit 0), not
an error. A match in one field wins over a missing field. Signed
PDFs parse normally; the signature field is ignored and signature
validity is not checked. The test builds a signature field with
pypdf (placeholders, not a real signature), so no binary fixture
is needed. Encrypted PDFs are tried with an empty
password; anything still locked is exit 4.

Its code lives in `src/pdf_form_check/` (one module per job:
reader, resolver, matcher, checker, CLI) and its own tests live in
`tests/pdf_form_check/` (`test_matcher.py`,
`test_checker.py`, `test_cli.py`, `test_field_resolver.py`,
`pdf_factory.py`, `fixtures/`).

## Adding a new app

1. `src/<package>/` (package = app name with `-` replaced by `_`)
   with `meta.py` (`APP_NAME` and `__version__` — the app's own
   name and version, re-exported by `__init__.py`), `__main__.py`
   (`main()` +
   `if __name__ == "__main__": raise SystemExit(main())`),
   `config/config.json`, and `assets/` if it needs bundled files.
2. `scripts/<package>/build.py` (copy an existing one, change
   `PACKAGE`). The central `scripts/build.py` picks it up
   automatically — no edit needed there. Override a
   `DefaultBuildConfig` attribute in the subclass if the app needs
   something special (windowed, hidden imports, extra data, icon,
   folder names).
3. A `[project.scripts]` entry in `pyproject.toml` (optional), plus a
   `[tool.setuptools.package-data]` entry if the app has config/assets
   (copy an existing app's line).

## Adding a new shared library

1. `src/common/<lib>/` with `__init__.py` re-exporting its public API.
2. Import it from any app. No `pyproject.toml` change needed unless it
   adds a dependency — then add it to `dependencies` (all apps) or
   document it as app-specific.

## Tests

```bash
pytest            # build tests (fixtures) + pdf-form-check's tests
ruff check src tests scripts
ruff format --check src tests scripts
```

The build tests deliberately cover only the build scripts and their
output, using fixture apps in tmp dirs — never the shipped sample
apps or shared libraries — so they stay valid whatever apps the
repo holds. pdf-form-check is the exception: it has real app tests
(checker, matcher, resolver, CLI) because its exit-code contract is
the feature, and those live in `tests/pdf_form_check/` — app tests
are grouped by directory (`tests/<package>/`), while the build tests
stay at `tests/` root.
