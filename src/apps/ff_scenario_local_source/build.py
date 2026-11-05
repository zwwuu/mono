from apps.ff_scenario_local_source.meta import APP_NAME, __version__
from common.build_config.build_config import DefaultBuildConfig


class FfScenarioLocalSourceBuild(DefaultBuildConfig):
    APP_NAME = APP_NAME
    VERSION = __version__
    CONSOLE = True
    EXTRA_DATAS = (("src/apps/ff_scenario_local_source/scenario.json", "scenario.json"),)


if __name__ == "__main__":
    FfScenarioLocalSourceBuild().main()
