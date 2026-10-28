"""Build pdf-form-check into an executable with PyInstaller — no .spec file.

Same shape as scripts/app_one/build.py: a subclass of the default build
config in scripts/build_config.py; only PACKAGE differs. To
customize an app, override a DefaultBuildConfig attribute in the
subclass (see scripts/app_one/build.py).

Usage (from anywhere):
    python scripts/pdf_form_check/build.py [--clean] [--no-zip]
    python scripts/pdf_form_check/build.py --name pdf-form-check-dev
    python scripts/build.py pdf-form-check     # or build every app from the root
"""

import importlib.util
import os

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_spec = importlib.util.spec_from_file_location(
    "build_config", os.path.join(SCRIPTS_DIR, "build_config.py")
)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
DefaultBuildConfig = _module.DefaultBuildConfig


class PdfFormCheckBuild(DefaultBuildConfig):
    PACKAGE = "pdf_form_check"


if __name__ == "__main__":
    PdfFormCheckBuild().main()
