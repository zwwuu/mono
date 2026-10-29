"""Build app-one.

Only PACKAGE (and overrides) live here. The app name and version are
read from src/apps/app_one/meta.py by DefaultBuildConfig, and the
entry point is src/apps/app_one/__main__.py. scripts/ is not a
package, so build_config.py is loaded from the parent directory
instead of imported relatively.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from build_config import DefaultBuildConfig


class AppOneBuild(DefaultBuildConfig):
    PACKAGE = "apps.app_one"
    CONSOLE = True
    # disclaimer.txt sits at the package root, not in a data folder,
    # so it is bundled explicitly, flat next to the exe.
    EXTRA_DATAS = (("src/apps/app_one/disclaimer.txt", "disclaimer.txt"),)


if __name__ == "__main__":
    AppOneBuild().main()
