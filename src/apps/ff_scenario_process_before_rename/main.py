"""Scenario: processing happens before rename and copy — headless FileFlow pipeline app.

The pipeline is defined by scenario.json in this directory. It is
resolved with FileFlow's build_pipeline() at runtime and executed
headlessly (no DB, dashboard, or scheduler).
"""

import json

from apps.fileflow.core.headless import run_pipeline
from apps.fileflow.core.tasks import build_pipeline, validate_task
from common.app_config import AppConfig

from . import meta


class FfScenarioProcessBeforeRenameApp(AppConfig):
    APP_NAME = meta.APP_NAME
    VERSION = meta.__version__
    DESCRIPTION = """Scenario: processing happens before rename and copy."""

    WITH_CONFIG = True

    def extra_args(self, parser):
        parser.add_argument(
            "--workdir",
            default=None,
            help="run working directory (default: ./<app>-work)",
        )

    def run(self):
        """Execute the scenario's pipeline headlessly.

        --config (JSON) overrides the scenario: "source" replaces the
        task source, "recipients" replaces notify recipients, and
        "smtp"/"ftp"/"smb" sections configure those backends.
        """
        scenario = json.loads(
            (self.ROUTER / "scenario.json").read_text(encoding="utf-8")
        )
        validate_task(scenario)
        task = {
            "id": scenario["id"],
            "name": scenario.get("name", scenario["id"]),
            "source": scenario.get("source", {}),
        }
        if scenario.get("recipients"):
            task["recipients"] = scenario["recipients"]
        if scenario.get("run_limit"):
            task["run_limit"] = scenario["run_limit"]
        config = dict(self.CONFIG or {})
        if "source" in config:
            task["source"] = config["source"]
        if "recipients" in config:
            task["recipients"] = config["recipients"]
        workdir = self.args.workdir or f"./{self.APP_NAME}-work"
        return run_pipeline(
            build_pipeline(scenario), task, config, self.LOGGER, workdir
        )


def main(argv=None):
    return FfScenarioProcessBeforeRenameApp.main(anchor=__file__, argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
