# app-three — url health check

URL health check: checks URLs with retry, writes a JSON report, emails on failure.

## Layout

- `pyproject.toml` — this app's own project file (name, version read
  from `src/app_three/meta.py`, console command, package data).
- `build.py` — builds this app into an exe (a subclass of the
  template's `DefaultBuildConfig`).
- `src/app_three/` — the app package: code, `meta.py`, `config/`, `assets/`.

## Run

From the monorepo root (shared libraries come from the root install):

```bash
pip install -e .              # shared libraries
pip install -e apps/app-three     # this app
python -m app_three --help        # or: app-three
```

## Build

```bash
python apps/app-three/build.py            # this app only
python scripts/build.py app-three         # or via the dispatcher
```

## Copied out?

This folder is a complete project file-wise, but the app imports
`common.*` from the monorepo, and its `build.py` loads
`scripts/build_config.py` from the template root. A standalone copy
also needs `src/common/` (to run) and `scripts/build_config.py` (to
build) alongside it.
