"""app-one entry point: report sender.

Relative imports to the app's own package, absolute imports to
common/. The PyInstaller entry is __main__.py, which imports this
module as apps.app_one.main, so the relative imports resolve both
from source (`python -m apps.app_one`) and in the exe.
"""

from common.app_config import AppConfig
from common.mail import MailClient, MailConfig
from common.utils import load_app_config

from . import APP_NAME, __version__
from .report import build_report

DEFAULT_CONFIG = {
    "subject": "Daily report",
    "recipients": ["ops@example.com"],
    "greeting": "Hello",
}

FALLBACK_TEMPLATE = "$greeting,\n\n$app report for $date.\n"


class AppOneApp(AppConfig):
    APP_NAME = APP_NAME
    VERSION = __version__
    DESCRIPTION = "Build a report and email it."
    WITH_DRY_RUN = True

    def extra_args(self, parser):
        parser.add_argument(
            "--config-dir", default=None, help="directory holding config.json (overrides)"
        )

    def load_config(self):
        search_dirs = [self.args.config_dir] if self.args.config_dir else None
        return load_app_config(APP_NAME, defaults=DEFAULT_CONFIG, search_dirs=search_dirs)

    def run(self):
        args, config, log = self.args, self.CONFIG, self.LOGGER

        template_file = self.ROUTER / "assets" / "email_template.txt"
        template = (
            template_file.read_text(encoding="utf-8")
            if template_file.is_file()
            else FALLBACK_TEMPLATE
        )
        report = build_report(config, template)

        # Single txt file bundled via EXTRA_DATAS: appended as the footer.
        disclaimer_file = self.ROUTER / "disclaimer.txt"
        if disclaimer_file.is_file():
            report = (
                report.rstrip("\n")
                + "\n\n"
                + disclaimer_file.read_text(encoding="utf-8").strip()
                + "\n"
            )
        else:
            log.warning("bundled disclaimer.txt not found, sending without footer")
        log.info("report built:\n%s", report)

        self.MAILER = MailClient(MailConfig.from_env(), dry_run=args.dry_run)
        sent = self.MAILER.send(config["recipients"], config["subject"], report)
        if not sent:
            log.error("failed to send report")
            return 1
        log.info("done")
        return 0


def main(argv=None):
    return AppOneApp.main(anchor=__file__, argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
