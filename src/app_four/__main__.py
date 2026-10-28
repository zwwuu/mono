"""app-four entry point: duplicate-file finder.

Absolute imports only — see app_one.__main__ for why. Read-only: it
reports duplicates, it never deletes. Exit code is always 0.
"""

import argparse
from pathlib import Path

from app_four import APP_NAME, __version__
from app_four.duplicates import build_summary, find_duplicates, format_summary
from common.logging import get_logger, setup_logging
from common.mail import MailClient, MailConfig
from common.utils import load_app_config, read_app_asset, save_json

DEFAULT_CONFIG = {
    "folder": ".",
    "pattern": "*",
    "min_size_bytes": 0,
    "output": "duplicates.json",
    "recipients": ["ops@example.com"],
}


def _load_exclude_patterns():
    """Exclude patterns from the bundled assets/exclude.txt, one per line."""
    text = read_app_asset(APP_NAME, "exclude.txt") or ""
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(prog=APP_NAME, description="Find duplicate files in a folder.")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument(
        "--config-dir", default=None, help="directory holding config.json (overrides)"
    )
    parser.add_argument("--folder", default=None, help="folder to scan (overrides config)")
    parser.add_argument("--pattern", default=None, help="glob pattern (overrides config)")
    parser.add_argument("--log-level", default="INFO", help="DEBUG, INFO, WARNING, ...")
    args = parser.parse_args(argv)

    setup_logging(level=args.log_level, name=APP_NAME)
    log = get_logger(APP_NAME)

    search_dirs = [args.config_dir] if args.config_dir else None
    config = load_app_config(APP_NAME, defaults=DEFAULT_CONFIG, search_dirs=search_dirs)
    folder = args.folder or config["folder"]
    pattern = args.pattern or config["pattern"]
    if not Path(folder).is_dir():
        log.warning("folder does not exist: %s (nothing to scan)", folder)

    groups = find_duplicates(
        folder,
        pattern,
        min_size=int(config["min_size_bytes"]),
        exclude=_load_exclude_patterns(),
    )
    summary = build_summary(folder, groups)
    save_json(config["output"], summary)
    log.info(
        "%d duplicate group(s), %d file(s), %d wasted bytes -> %s",
        summary["total_groups"],
        summary["total_files"],
        summary["total_wasted_bytes"],
        config["output"],
    )
    if groups:
        mail = MailClient(MailConfig.from_env())
        if not mail.send(
            config["recipients"], f"{APP_NAME} duplicate report", format_summary(summary)
        ):
            log.error("failed to send duplicate report")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
