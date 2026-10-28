"""Build app-one into an executable with PyInstaller — no .spec file.

This script is just a subclass of the default build config in
scripts/build_config.py: it sets the package and inherits
everything else. The app name is read from the package's own
APP_NAME, so the name is defined once. To customize an app,
override a DefaultBuildConfig attribute — e.g. CONSOLE = False for
a windowed GUI app, or HIDDEN_IMPORTS = [...] for dynamic imports.

Usage (from anywhere):
    python scripts/app_one/build.py [--clean] [--no-zip]
    python scripts/app_one/build.py --name app-one-dev
    python scripts/build.py app-one     # or build every app from the root
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


class AppOneBuild(DefaultBuildConfig):
    PACKAGE = "app_one"
    # A single standalone txt file, placed flat next to the exe.
    # Single files work the same way as folders in EXTRA_DATAS: the
    # pair names the file's full path next to the exe.
    EXTRA_DATAS = (("src/app_one/disclaimer.txt", "disclaimer.txt"),)


if __name__ == "__main__":
    AppOneBuild().main()
