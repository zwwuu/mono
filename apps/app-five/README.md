# app-five — note indexer

Note indexer: indexes a folder of Markdown notes into a JSON index.

## Layout

- `pyproject.toml` — this app's own project file (name, version read
  from `src/app_five/meta.py`, console command, package data).
- `build.py` — builds this app into an exe (a subclass of the
  template's `DefaultBuildConfig`).
- `src/app_five/` — the app package: code, `meta.py`, `config/`, `assets/`.

## Run

From the monorepo root (shared libraries come from the root install):

```bash
pip install -e .              # shared libraries
pip install -e apps/app-five     # this app
python -m app_five --help        # or: app-five
```

## Build

```bash
python apps/app-five/build.py            # this app only
python scripts/build.py app-five         # or via the dispatcher
```

## Copied out?

This folder is a complete project file-wise, but the app imports
`common.*` from the monorepo, and its `build.py` loads
`scripts/build_config.py` from the template root. A standalone copy
also needs `src/common/` (to run) and `scripts/build_config.py` (to
build) alongside it.
