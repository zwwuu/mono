from apps.app_one.meta import APP_NAME, __version__
from common.build_config.build_config import DefaultBuildConfig


class AppOneBuild(DefaultBuildConfig):
    APP_NAME = APP_NAME
    VERSION = __version__
    CONSOLE = True
    EXTRA_DATAS = (("src/apps/app_one/disclaimer.txt", "disclaimer.txt"),)


if __name__ == "__main__":
    AppOneBuild().main()
