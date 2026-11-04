"""app-one entry point: report sender.

Relative imports to the app's own package, absolute imports to
common/. The PyInstaller entry is __main__.py, which imports this
module as apps.app_one.main, so the relative imports resolve both
from source (`python -m apps.app_one`) and in the exe.
"""

from common.cli import build_parser, resolve_log_level
from common.logging import log, setup_logging
from common.mail import MailClient, MailConfig
from common.utils import app_file_path, load_app_config, read_app_asset

from . import APP_NAME, __version__
from .report import build_report

DEFAULT_CONFIG = {
    "subject": "Daily report",
    "recipients": ["ops@example.com"],
    "greeting": "Hello",
}


def main(argv=None):
    parser = build_parser(
        APP_NAME, "Build a report and email it.", __version__, with_dry_run=True
    )
    parser.add_argument(
        "--config-dir", default=None, help="directory holding config.json (overrides)"
    )
    args = parser.parse_args(argv)

    setup_logging(level=resolve_log_level(args))

    search_dirs = [args.config_dir] if args.config_dir else None
    config = load_app_config(APP_NAME, defaults=DEFAULT_CONFIG, search_dirs=search_dirs)

    template = (
        read_app_asset(APP_NAME, "email_template.txt") or "$greeting,\n\n$app report for $date.\n"
    )
    report = build_report(config, template)

    # Single txt file bundled via EXTRA_DATAS: appended as the footer.
    disclaimer_path = app_file_path(APP_NAME, "disclaimer.txt")
    if disclaimer_path:
        with open(disclaimer_path, encoding="utf-8") as f:
            report = report.rstrip("\n") + "\n\n" + f.read().strip() + "\n"
    else:
        log.warning("bundled disclaimer.txt not found, sending without footer")
    log.info("report built:\n%s", report)

    mail = MailClient(MailConfig.from_env(), dry_run=args.dry_run)
    sent = mail.send(config["recipients"], config["subject"], report)
    if not sent:
        log.error("failed to send report")
        return 1
    log.info("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
