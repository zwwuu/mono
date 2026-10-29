"""Entry point: `python -m apps.pdf_form_check`, and the PyInstaller entry.

Absolute import on purpose: PyInstaller runs the entry script in a
context where relative imports would fail, while the app's own
modules (imported here as apps.pdf_form_check.cli) keep their
relative imports. This is the file scripts/pdf_form_check/build.py
builds.
"""

from apps.pdf_form_check.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
