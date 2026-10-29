"""Build app-one.

Only PACKAGE (and overrides) live here. The app name and version are
read from src/apps/app_one/meta.py by DefaultBuildConfig, and the
entry point is src/apps/app_one/__main__.py.

Run as a module from the repo root, never as a file:
`python -m scripts.app_one.build` (or via scripts/build.py).
That is what makes the relative import below work — scripts/ is a
package, so no sys.path manipulation is needed.
"""

from ..build_config import DefaultBuildConfig


class AppOneBuild(DefaultBuildConfig):
    PACKAGE = "apps.app_one"
    CONSOLE = True
    # disclaimer.txt sits at the package root, not in a data folder,
    # so it is bundled explicitly, flat next to the exe.
    EXTRA_DATAS = (("src/apps/app_one/disclaimer.txt", "disclaimer.txt"),)


if __name__ == "__main__":
    AppOneBuild().main()
