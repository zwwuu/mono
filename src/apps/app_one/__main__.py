"""Entry point: `python -m apps.app_one`, and the PyInstaller entry.

Absolute import on purpose: PyInstaller runs the entry script in a
context where relative imports would fail, while the app's own
modules (imported here as apps.app_one.main) keep their relative
imports. This is the file scripts/app_one/build.py builds.
"""

from apps.app_one.main import main

if __name__ == "__main__":
    raise SystemExit(main())
