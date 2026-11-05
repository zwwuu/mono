"""App framework for linear (run-to-completion) CLI apps.

A linear app parses args, loads config, sets up logging, and runs
start-to-finish — no server loop. pdf_form_check, sftp_uploader,
app_one, and the sysinfo CLI all fit this shape.

Inherit and declare; the framework owns the lifecycle:

    from common.app_config import AppConfig

    class SysinfoApp(AppConfig):
        APP_NAME = "sysinfo"
        VERSION = "0.1.0"
        DESCRIPTION = "Print runtime system info."
        WITH_VERBOSE = True

        def run(self):
            self.LOGGER.info("starting %s", self.APP_NAME)
            ...

    def main(argv=None):
        return SysinfoApp.main(anchor=__file__, argv=argv)

AppConfig.main() bootstraps the app, then calls setup() -> run() ->
teardown(). It returns the exit code: run()'s return value, 130 on
KeyboardInterrupt, 1 on unhandled error (logged with traceback).
teardown() always runs, even when run() fails.

Customization points (override in the subclass):
- extra_args(parser): add app-specific CLI flags.
- load_config(): validated configs; default loads the JSON file when
  WITH_CONFIG (a missing file is exit code 2).
- setup()/teardown(): optional hooks around run().

Components: self.args, self.CONFIG, self.LOGGER, self.ROUTER (the
path resolver — next to the exe when frozen, otherwise the anchor
file's directory; app code never touches __file__ or sys.frozen).
self.MAILER is a slot the app fills itself (e.g. a
common.mail.MailClient).
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
    # -- identity: declared by each app's subclass (required) --
    APP_NAME = None
    VERSION = None
    DESCRIPTION = None

    # -- CLI surface: opt in to standard flags --
    WITH_CONFIG = False
    WITH_DRY_RUN = False
    WITH_VERBOSE = False
    CONFIG_DEFAULT = None  # config path used when --config is absent

    # -- components: filled by the framework --
    CONFIG = None
    ROUTER = None  # the path resolver
    LOGGER = None

    # -- components: filled by the app itself --
    MAILER = None

    def __init__(self, anchor, argv=None):
        """
        Args:
            anchor: __file__ of the app's entry module — the directory
                data files live next to from source.
            argv: CLI args (None = sys.argv).
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
            with_config=self.WITH_CONFIG,
            with_dry_run=self.WITH_DRY_RUN,
            with_verbose=self.WITH_VERBOSE,
        )
        self.extra_args(parser)
        self.args: argparse.Namespace = parser.parse_args(argv)

        self.CONFIG = self.load_config()

        setup_logging(level=resolve_log_level(self.args))
        self.LOGGER = log

        self.ROUTER = Router(base=_resolve_base(anchor))

    # -- customization points: override in the subclass --

    def extra_args(self, parser):
        """Add app-specific CLI flags. Default: none."""

    def load_config(self):
        """Load the app's config. Default: parsed JSON dict when
        WITH_CONFIG, else None. A missing file is exit code 2."""
        if not self.WITH_CONFIG:
            return None
        path = self.args.config or self.CONFIG_DEFAULT
        if not path:
            return {}
        try:
            return load_json(path)
        except ConfigError as exc:
            print(f"config error: {exc}", file=sys.stderr)
            raise SystemExit(2)

    # -- lifecycle: the framework calls these in order --

    def setup(self):
        """Optional hook: runs after bootstrap, before run()."""

    def run(self):
        """The app's logic. self.args/self.CONFIG/self.LOGGER/self.ROUTER
        are ready. Subclasses must implement this."""
        raise NotImplementedError(f"{type(self).__name__} must implement run()")

    def teardown(self):
        """Optional hook: always runs after run(), even on error."""

    @classmethod
    def main(cls, anchor, argv=None):
        """Entry point: bootstrap, then setup() -> run() -> teardown().

        Returns the exit code: run()'s return value, 130 on
        KeyboardInterrupt, 1 on unhandled error (logged).
        """
        app = cls(anchor=anchor, argv=argv)
        try:
            app.setup()
            return app.run()
        except KeyboardInterrupt:
            app.LOGGER.warning("interrupted by user")
            return 130
        except SystemExit:
            raise
        except Exception:
            app.LOGGER.exception("unhandled error")
            return 1
        finally:
            try:
                app.teardown()
            except Exception:
                app.LOGGER.exception("teardown failed")
