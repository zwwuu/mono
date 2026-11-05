"""Bootstrap template for linear (run-to-completion) apps.

A linear app parses args, loads config, sets up logging, and runs
start-to-finish — no server loop. pdf_form_check, sftp_uploader,
app_one, and the sysinfo CLI all fit this shape.

Subclass per app, declaring identity as class attributes:

    from common.app_config import AppConfig

    class SysinfoConfig(AppConfig):
        APP_NAME = "sysinfo"
        VERSION = "0.1.0"
        DESCRIPTION = "Print runtime system info."

    def main(argv=None):
        cfg = SysinfoConfig(anchor=__file__, argv=argv, with_verbose=True)
        cfg.LOGGER.info("starting %s %s", cfg.APP_NAME, cfg.VERSION)
        out = cfg.ROUTER / "assets" / "report.txt"
        ...

In order, the constructor:
1. Parses CLI args via common.cli.build_parser (--version/--log-level
   always; opt in to --config/--dry-run/--verbose; extra_args(parser)
   adds app-specific flags). Available as cfg.args.
2. Loads config into cfg.CONFIG: None when the app takes no config
   file; the parsed JSON dict when --config is used (a missing file
   is exit code 2); or whatever load_config(cfg) returns for
   validated configs.
3. Sets up the shared static log and stores it as cfg.LOGGER (level
   from --log-level, DEBUG with --verbose). Apps that drive the level
   from the config file can call setup_logging() again afterwards —
   it is idempotent.
4. Resolves cfg.ROUTER once: the path resolver — next to the exe
   when frozen, otherwise the anchor file's directory. App code
   never touches __file__ or sys.frozen directly.

ROUTER and MAILER are component slots the app fills itself (e.g. a
FastAPI router, a common.mail.MailClient) — the template leaves them
None.
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from common.cli import build_parser, resolve_log_level
from common.config import ConfigError, load_json
from common.logging import log, setup_logging
from common.utils.paths import exe_dir, is_frozen

__all__ = ["AppConfig", "Router"]


@dataclass(frozen=True)
class Router:
    """Path resolver for one app, resolved once at startup."""

    base: Path

    def __truediv__(self, part):
        """cfg.ROUTER / "templates" / "x.html"."""
        return self.base / part

    def join(self, *parts):
        """cfg.ROUTER.join("templates", "x.html")."""
        return self.base.joinpath(*parts)


def _resolve_base(anchor):
    if is_frozen():
        return Path(exe_dir())
    return Path(anchor).resolve().parent


class AppConfig:
    # Declared by each app's subclass:
    APP_NAME = None
    VERSION = None
    DESCRIPTION = None

    # Component slots filled by the constructor: CONFIG, LOGGER,
    # ROUTER (the path resolver). MAILER is filled by the app itself.
    CONFIG = None
    ROUTER = None
    MAILER = None
    LOGGER = None

    def __init__(
        self,
        *,
        anchor,
        argv=None,
        with_config=False,
        with_dry_run=False,
        with_verbose=False,
        extra_args=None,
        load_config=None,
        config_default=None,
    ):
        """
        Args:
            anchor: __file__ of the app's entry module — the directory
                data files live next to from source.
            argv: CLI args (None = sys.argv).
            with_config/with_dry_run/with_verbose: opt-in standard flags.
            extra_args: callable(parser) adding app-specific flags.
            load_config: callable(cfg) -> config object, for validated
                configs. Defaults to raw JSON dict loading.
            config_default: config path used when --config is absent.
        """
        if not self.APP_NAME:
            raise TypeError(
                f"{type(self).__name__} must set APP_NAME (subclass AppConfig "
                "and declare APP_NAME/VERSION/DESCRIPTION)"
            )
        if not self.VERSION:
            raise TypeError(f"{type(self).__name__} must set VERSION")

        parser = build_parser(
            self.APP_NAME,
            self.DESCRIPTION or "",
            self.VERSION,
            with_config=with_config,
            with_dry_run=with_dry_run,
            with_verbose=with_verbose,
        )
        if extra_args is not None:
            extra_args(parser)
        self.args: argparse.Namespace = parser.parse_args(argv)

        if load_config is not None:
            self.CONFIG = load_config(self)
        elif with_config:
            self.CONFIG = self._load_json_config(config_default)
        else:
            self.CONFIG = None

        setup_logging(level=resolve_log_level(self.args))
        self.LOGGER = log

        self.ROUTER = Router(base=_resolve_base(anchor))

    def _load_json_config(self, default):
        path = self.args.config or default
        if not path:
            return {}
        try:
            return load_json(path)
        except ConfigError as exc:
            print(f"config error: {exc}", file=sys.stderr)
            raise SystemExit(2)
