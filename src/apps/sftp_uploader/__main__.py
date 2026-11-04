"""Command-line entry point: ``python -m sftp_uploader``."""
from __future__ import annotations

import sys

from common.cli import build_parser
from common.logging import log, setup_logging

from . import __version__
from .config import ConfigError, load_config
from .context import RunContext
from .pipeline import Pipeline
from .steps import FindFileStep


def setup_logging_from_config(config, verbose=False):
    """Configure the shared static log from the ``logging`` config section.

    ``verbose`` forces DEBUG regardless of the config file's logging.level.
    """
    lcfg = config.get("logging", {}) or {}
    level = "DEBUG" if verbose else lcfg.get("level", "INFO")
    return setup_logging(
        level=level,
        log_file=lcfg.get("file"),
        max_bytes=int(lcfg.get("max_bytes", 1_048_576)),
        backup_count=int(lcfg.get("backup_count", 3)),
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser(
        prog="sftp-uploader",
        description="Find a local file by pattern and upload it to an SFTP server.",
        version=__version__,
        with_config=True,
        with_dry_run=True,
        with_verbose=True,
    )
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    setup_logging_from_config(config, verbose=args.verbose)
    if args.verbose:
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
