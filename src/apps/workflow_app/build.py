from apps.workflow_app.meta import APP_NAME, __version__
from common.build_config.build_config import DefaultBuildConfig


class WorkflowAppBuild(DefaultBuildConfig):
    APP_NAME = APP_NAME
    VERSION = __version__
    CONSOLE = True
    EXTRA_DATAS = (
        ("src/apps/workflow_app/templates", "templates"),
        ("src/apps/workflow_app/static", "static"),
    )


if __name__ == "__main__":
    WorkflowAppBuild().main()
