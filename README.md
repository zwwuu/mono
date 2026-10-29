# Python monorepo

Several apps sharing common libraries, built to executables with
PyInstaller. One install, one dependency set, one build system —
and each app still versions independently, via its own `meta.py`.

## Layout

```text
pyproject.toml            # one distribution: apps.* + common.*, console scripts
src/
  apps/
    app_one/              # an app: __main__.py, main.py, meta.py, config/, assets/
    pdf_form_check/       # another app, same shape
  common/                 # shared libraries: logging/, mail/, utils/
scripts/
  build.py                # build all apps, or one: python scripts/build.py [app]
  build_config.py         # the shared PyInstaller build (subclass it per app)
  app_one/build.py        # tiny subclass: PACKAGE = "apps.app_one", + overrides
  pdf_form_check/build.py # tiny subclass: PACKAGE = "apps.pdf_form_check"
tests/
  pdf_form_check/         # tests for that app
  test_build_config.py    # tests for scripts/build_config.py
  test_central_build.py   # tests for scripts/build.py
```

The layout rule that makes the build work, and the one that was
broken before: **the import path matches the directory path**.
Apps live in `src/apps/<package>/`, so they are imported as
`apps.<package>` — not as top-level `<package>`. That applies
everywhere, consistently:

- `pyproject.toml` entry points: `apps.app_one.main:main`,
  `apps.pdf_form_check.cli:main`
- app `__init__.py`: `from .meta import APP_NAME, __version__`
  (never `from <package>.meta`, which would be a different,
  nonexistent top-level package)
- tests: `from apps.pdf_form_check.checker import ...`
- `common.utils.paths.repo_app_dir()`: `src/apps/<package>/`
- `scripts/<app>/build.py`: `PACKAGE = "apps.<package>"`, and the
  PyInstaller entry point is `src/apps/<package>/__main__.py`

`scripts/` is a package (with `__init__.py` files), so per-app
build modules import `build_config.py` relatively
(`from ..build_config import DefaultBuildConfig`) — no `sys.path`
manipulation. Run them as modules from the repo root
(`python -m scripts.app_one.build`), never as files, and the
central build runs them the same way.

## Run from source

```sh
pip install -e ".[dev]"
python -m apps.app_one --dry-run
python -m apps.pdf_form_check form.pdf   # exit 1 when A1 or A2 is "yes"
pytest
```

`pip install -e .` also installs the console commands `app-one`
and `pdf-form-check`.

## Build exes

```sh
python scripts/build.py                 # every app
python scripts/build.py app-one         # one app
python scripts/build.py app-one --clean
python scripts/build.py all --no-zip    # skip the zip bundle
```

Each build runs PyInstaller `--onedir` with `src/` on `--paths`,
then copies the app's data folders (`config/`, `assets/`) and any
`EXTRA_DATAS` flat next to the exe — PyInstaller's `--add-data`
is never used, because it would bury data in `_internal` instead.
The result is `dist/<app>/` plus a zip
`dist/<app>-<version>-<timestamp>.zip`. No `.spec` files are
written into the repo: the generated spec goes to the build dir
via `--specpath`.

Frozen apps find their data the same way source runs do, through
`common.utils.app_file_path()` / `load_app_config()`: next to the
exe when frozen, under `src/apps/<package>/` from source.

## Add a new app

1. Create `src/apps/<package>/` with `__init__.py` (re-exporting
   `meta.py`), `meta.py` (`APP_NAME`, `__version__`), and
   `__main__.py` (absolute import of the app's `main`, e.g.
   `from apps.<package>.main import main`).
2. Create `scripts/<package>/` with an (empty) `__init__.py` and a
   `build.py` as a `DefaultBuildConfig` subclass with
   `PACKAGE = "apps.<package>"` (add `EXTRA_DATAS` for files
   outside `config/` and `assets/`).
3. Add its console script to `pyproject.toml` and its tests under
   `tests/<package>/`. Nothing else needs editing: the central
   build discovers apps by scanning `scripts/*/build.py`.

## Versions

The root `pyproject.toml` version (0.1.0) is the distribution
version and is decorative here — nothing is published to PyPI.
The version that matters per app is its own `meta.py`
`__version__`: the app prints it (`--version`), and the build
reads it for the zip name. A per-app build script should not
re-declare it; `DefaultBuildConfig` reads it from `meta.py`.

## Why not uv workspaces / Pants / Bazel?

Python and `pyproject.toml` have no native monorepo concept: one
pyproject equals one distribution. uv workspaces add per-app
pyprojects and one lockfile, and Pants/Bazel add fine-grained
targets and affected-only builds — both are real, and both are
machinery this repo does not yet need. With a handful of small
apps, one owner, and one release rhythm, one pyproject plus the
`scripts/` build above is the simple layout, and per-app
independence is kept where it matters (name, version, entry
point, data files). If apps ever need to be installed or
published separately, or need incompatible dependencies, that is
the point to migrate to uv workspaces.
