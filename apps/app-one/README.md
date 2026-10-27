# app-one — report sender

Report sender: builds a short report from a bundled template and emails it.

## Layout

- `pyproject.toml` — this app's own project file (name, version read
  from `src/app_one/meta.py`, console command, package data).
- `build.py` — builds this app into an exe (a subclass of the
  template's `DefaultBuildConfig`).
- `src/app_one/` — the app package: code, `meta.py`, `config/`, `assets/`.

## Run

From the monorepo root (shared libraries come from the root install):

```bash
pip install -e .              # shared libraries
pip install -e apps/app-one     # this app
python -m app_one --help        # or: app-one
```

## Build

```bash
python apps/app-one/build.py            # this app only
python scripts/build.py app-one         # or via the dispatcher
```

## Copied out?

This folder is a complete project file-wise, but the app imports
`common.*` from the monorepo, and its `build.py` loads
`scripts/build_config.py` from the template root. A standalone copy
also needs `src/common/` (to run) and `scripts/build_config.py` (to
build) alongside it.
