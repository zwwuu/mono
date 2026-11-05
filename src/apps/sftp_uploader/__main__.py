"""Command-line entry point: ``python -m sftp_uploader``."""
from __future__ import annotations

import sys

from common.app_config import AppConfig
from common.logging import setup_logging

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


class SftpUploaderApp(AppConfig):
    APP_NAME = "sftp-uploader"
    VERSION = __version__
    DESCRIPTION = "Find a local file by pattern and upload it to an SFTP server."
    WITH_CONFIG = True
    WITH_DRY_RUN = True
    WITH_VERBOSE = True

    def load_config(self):
        try:
            return load_config(self.args.config)
        except ConfigError as exc:
            print(f"config error: {exc}", file=sys.stderr)
            raise SystemExit(2)

    def setup(self):
        # Logging level comes from the config file (not just CLI flags),
        # so re-apply it here — setup_logging() is idempotent.
        setup_logging_from_config(self.CONFIG, verbose=self.args.verbose)
        if self.args.verbose:
            self.LOGGER.debug("verbose: DEBUG logging forced by --verbose")
        self.LOGGER.info(
            "starting sftp-uploader %s (config: %s)", self.VERSION, self.args.config
        )

    def run(self):
        if self.args.dry_run:
            ctx = RunContext(self.CONFIG, attempt=1)
            try:
                FindFileStep().run(ctx)
            except FileNotFoundError as exc:
                self.LOGGER.error("dry-run: %s", exc)
                return 3
            self.LOGGER.info("dry-run: would upload %s", ctx.local_path)
            print(f"would upload: {ctx.local_path}")
            return 0
        return Pipeline(self.CONFIG).execute()


def main(argv: list[str] | None = None) -> int:
    return SftpUploaderApp.main(anchor=__file__, argv=argv)


if __name__ == "__main__":
    sys.exit(main())
