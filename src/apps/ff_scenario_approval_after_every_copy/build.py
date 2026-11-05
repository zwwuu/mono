from apps.ff_scenario_approval_after_every_copy.meta import APP_NAME, __version__
from common.build_config.build_config import DefaultBuildConfig


class FfScenarioBuildrovalAfterEveryCopyBuild(DefaultBuildConfig):
    APP_NAME = APP_NAME
    VERSION = __version__
    CONSOLE = True
    EXTRA_DATAS = (("src/apps/ff_scenario_approval_after_every_copy/scenario.json", "scenario.json"),)


if __name__ == "__main__":
    FfScenarioBuildrovalAfterEveryCopyBuild().main()
