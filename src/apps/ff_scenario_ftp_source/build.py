from apps.ff_scenario_ftp_source.meta import APP_NAME, __version__
from common.build_config.build_config import DefaultBuildConfig


class FfScenarioFtpSourceBuild(DefaultBuildConfig):
    APP_NAME = APP_NAME
    VERSION = __version__
    CONSOLE = True
    EXTRA_DATAS = (("src/apps/ff_scenario_ftp_source/scenario.json", "scenario.json"),)


if __name__ == "__main__":
    FfScenarioFtpSourceBuild().main()
