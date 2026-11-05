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


class AppOneConfig(AppConfig):
    APP_NAME = APP_NAME
    VERSION = __version__
    DESCRIPTION = "Build a report and email it."


def _extra_args(parser):
    parser.add_argument(
        "--config-dir", default=None, help="directory holding config.json (overrides)"
    )


def _load_config(cfg):
    search_dirs = [cfg.args.config_dir] if cfg.args.config_dir else None
    return load_app_config(APP_NAME, defaults=DEFAULT_CONFIG, search_dirs=search_dirs)


def main(argv=None):
    cfg = AppOneConfig(
        anchor=__file__,
        argv=argv,
        with_dry_run=True,
        extra_args=_extra_args,
        load_config=_load_config,
    )
    args, config, log = cfg.args, cfg.CONFIG, cfg.LOGGER

    template_file = cfg.ROUTER / "assets" / "email_template.txt"
    template = (
        template_file.read_text(encoding="utf-8")
        if template_file.is_file()
        else FALLBACK_TEMPLATE
    )
    report = build_report(config, template)

    # Single txt file bundled via EXTRA_DATAS: appended as the footer.
    disclaimer_file = cfg.ROUTER / "disclaimer.txt"
    if disclaimer_file.is_file():
        report = (
            report.rstrip("\n") + "\n\n" + disclaimer_file.read_text(encoding="utf-8").strip() + "\n"
        )
    else:
        log.warning("bundled disclaimer.txt not found, sending without footer")
    log.info("report built:\n%s", report)

    cfg.MAILER = MailClient(MailConfig.from_env(), dry_run=args.dry_run)
    sent = cfg.MAILER.send(config["recipients"], config["subject"], report)
    if not sent:
        log.error("failed to send report")
        return 1
    log.info("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
