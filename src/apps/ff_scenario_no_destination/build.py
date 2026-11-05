from apps.ff_scenario_no_destination.meta import APP_NAME, __version__
from common.build_config.build_config import DefaultBuildConfig


class FfScenarioNoDestinationBuild(DefaultBuildConfig):
    APP_NAME = APP_NAME
    VERSION = __version__
    CONSOLE = True
    EXTRA_DATAS = (("src/apps/ff_scenario_no_destination/scenario.json", "scenario.json"),)


if __name__ == "__main__":
    FfScenarioNoDestinationBuild().main()
