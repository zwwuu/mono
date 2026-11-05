from apps.ff_scenario_multi_approval.meta import APP_NAME, __version__
from common.build_config.build_config import DefaultBuildConfig


class FfScenarioMultiBuildrovalBuild(DefaultBuildConfig):
    APP_NAME = APP_NAME
    VERSION = __version__
    CONSOLE = True
    EXTRA_DATAS = (("src/apps/ff_scenario_multi_approval/scenario.json", "scenario.json"),)


if __name__ == "__main__":
    FfScenarioMultiBuildrovalBuild().main()
