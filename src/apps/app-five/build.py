"""Build app-five into an executable with PyInstaller — no .spec file.

Same shape as apps/app-one/build.py: a subclass of the default build
config in scripts/build_config.py; only PACKAGE differs. To
customize an app, override a DefaultBuildConfig attribute in the
subclass (see apps/app-one/build.py).

Usage (from anywhere):
    python apps/app-five/build.py [--clean] [--no-zip]
    python apps/app-five/build.py --name app-five-dev
    python scripts/build.py app-five     # or build every app from the root
"""

import importlib.util
import os

APP_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(APP_DIR))
_BUILD_CONFIG = os.path.join(REPO_ROOT, "scripts", "build_config.py")
if not os.path.isfile(_BUILD_CONFIG):
    raise SystemExit(
        f"build_config.py not found: {_BUILD_CONFIG} — this build script loads "
        "the shared config from the template root."
    )

_spec = importlib.util.spec_from_file_location("build_config", _BUILD_CONFIG)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
DefaultBuildConfig = _module.DefaultBuildConfig


class AppFiveBuild(DefaultBuildConfig):
    PACKAGE = "app_five"


if __name__ == "__main__":
    AppFiveBuild().main()
