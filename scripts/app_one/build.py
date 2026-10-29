"""Build app-one.

APP_NAME and VERSION are imported from the app's own meta.py — the
same constants the app itself re-exports — so they are defined
once. The central build puts src/ on PYTHONPATH for this module;
a direct run needs PYTHONPATH=src (or an editable install).

Run as a module from the repo root, never as a file:
`python -m scripts.app_one.build` (or via scripts/build.py).
"""

from apps.app_one.meta import APP_NAME, __version__

from ..build_config import DefaultBuildConfig


class AppOneBuild(DefaultBuildConfig):
    APP_NAME = APP_NAME
    VERSION = __version__
    PACKAGE = "apps.app_one"
    CONSOLE = True
    # disclaimer.txt sits at the package root, not in a data folder,
    # so it is bundled explicitly, flat next to the exe.
    EXTRA_DATAS = (("src/apps/app_one/disclaimer.txt", "disclaimer.txt"),)


if __name__ == "__main__":
    AppOneBuild().main()
