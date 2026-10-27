"""app-three entry point: URL health check.

Absolute imports only — see app_one.__main__ for why. Exit code is 1
when any URL is down, so the app works as a monitoring check.
"""

import argparse

from app_three import APP_NAME, __version__
from app_three.health import build_summary, check_all, format_summary
from common.logging import get_logger, setup_logging
from common.mail import MailClient, MailConfig
from common.utils import load_app_config, read_app_asset, save_json

DEFAULT_CONFIG = {
    "urls": ["https://example.com"],
    "timeout_seconds": 5,
    "max_workers": 5,
    "output": "health-report.json",
    "notify_on": "failure",
    "recipients": ["ops@example.com"],
}


def main(argv=None):
    parser = argparse.ArgumentParser(prog=APP_NAME, description="Check URLs and report health.")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument(
        "--config-dir", default=None, help="directory holding config.json (overrides)"
    )
    parser.add_argument("--log-level", default="INFO", help="DEBUG, INFO, WARNING, ...")
    args = parser.parse_args(argv)

    setup_logging(level=args.log_level, name=APP_NAME)
    log = get_logger(APP_NAME)

    search_dirs = [args.config_dir] if args.config_dir else None
    config = load_app_config(APP_NAME, defaults=DEFAULT_CONFIG, search_dirs=search_dirs)

    results = check_all(
        config["urls"],
        timeout=int(config["timeout_seconds"]),
        max_workers=int(config["max_workers"]),
    )
    summary = build_summary(results)
    save_json(config["output"], summary)
    log.info(
        "checked %d URL(s): %d up, %d down -> %s",
        summary["total"],
        summary["up"],
        summary["down"],
        config["output"],
    )
    for result in summary["results"]:
        if result["status"] != "UP":
            log.warning("DOWN %s: %s", result["url"], result["error"])

    notify_on = config.get("notify_on", "failure")
    if notify_on == "always" or (notify_on == "failure" and summary["down"]):
        notice = read_app_asset(APP_NAME, "notice.txt") or ""
        body = format_summary(summary) + "\n\n" + notice
        mail = MailClient(MailConfig.from_env())
        if not mail.send(config["recipients"], f"{APP_NAME} health report", body):
            log.error("failed to send health report")
            return 1
    return 1 if summary["down"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
