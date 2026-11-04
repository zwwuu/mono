"""Command-line entry point: ``python -m sftp_uploader``."""
from __future__ import annotations

import argparse
import logging
import sys

from . import __version__
from .config import ConfigError, load_config
from .context import RunContext
from .log import log, setup_logging
from .pipeline import Pipeline
from .steps import FindFileStep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="sftp-uploader",
        description="Find a local file by pattern and upload it to an SFTP server.",
    )
    ap.add_argument("--config", default="config.json", help="path to config.json (default: ./config.json)")
    ap.add_argument("--dry-run", action="store_true",
                    help="only run the find_file step and print what would be uploaded")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="DEBUG logging regardless of the config file's logging.level")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = ap.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    setup_logging(config)
    if args.verbose:
        log.setLevel(logging.DEBUG)
        for handler in log.handlers:
            handler.setLevel(logging.DEBUG)
        log.debug("verbose: DEBUG logging forced by --verbose")
    log.info("starting sftp-uploader %s (config: %s)", __version__, args.config)

    if args.dry_run:
        ctx = RunContext(config, attempt=1)
        try:
            FindFileStep().run(ctx)
        except FileNotFoundError as exc:
            log.error("dry-run: %s", exc)
            return 3
        log.info("dry-run: would upload %s", ctx.local_path)
        print(f"would upload: {ctx.local_path}")
        return 0

    try:
        return Pipeline(config).execute()
    except KeyboardInterrupt:
        log.warning("interrupted by user")
        return 130


if __name__ == "__main__":
    sys.exit(main())
