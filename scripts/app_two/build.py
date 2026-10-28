"""Build app-two into an executable with PyInstaller — no .spec file.

Same shape as scripts/app_one/build.py: a subclass of the default build
config in scripts/build_config.py; only PACKAGE differs. To
customize an app, override a DefaultBuildConfig attribute in the
subclass (see scripts/app_one/build.py).

Usage (from anywhere):
    python scripts/app_two/build.py [--clean] [--no-zip]
    python scripts/app_two/build.py --name app-two-dev
    python scripts/build.py app-two     # or build every app from the root
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


class AppTwoBuild(DefaultBuildConfig):
    PACKAGE = "app_two"
    # Arbitrary template files/folders, placed flat next to the exe.
    # Directories are bundled recursively; any name works per app.
    EXTRA_DATAS = (("src/app_two/templates", "templates"),)


if __name__ == "__main__":
    AppTwoBuild().main()
