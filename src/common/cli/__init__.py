"""Standard CLI flags shared by all apps (argparse, stdlib only).

Usage:
    from common.cli import build_parser, resolve_log_level
    from common.logging import setup_logging

    def main(argv=None):
        parser = build_parser("my-app", "Does things.", "1.0.0",
                              with_config=True, with_dry_run=True)
        parser.add_argument("--host", help="app-specific flags go here")
        args = parser.parse_args(argv)
        setup_logging(level=resolve_log_level(args))
        ...

Every parser gets ``--version`` and ``--log-level``. Opt in to
``--config``, ``--dry-run`` and ``--verbose``/``-v`` as the app needs
them; flags the app doesn't need stay out of its ``--help``.
"""

import argparse

__all__ = ["build_parser", "resolve_log_level"]


def build_parser(prog, description, version, *, with_config=False,
                 with_dry_run=False, with_verbose=False,
                 config_default="config.json"):
    """Build an ArgumentParser with the standard flags.

    Args:
        prog: program name (used for --version output and help).
        description: one-line description for --help.
        version: version string for --version.
        with_config: add --config (path to the JSON config file).
        with_dry_run: add --dry-run (log actions instead of doing them).
        with_verbose: add -v/--verbose (DEBUG logging, overrides --log-level).
        config_default: default value for --config.

    Add app-specific flags to the returned parser before parse_args().
    """
    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument("--version", action="version",
                        version=f"{prog} {version}")
    if with_config:
        parser.add_argument("--config", default=config_default,
                            help=f"path to config file (default: {config_default})")
    parser.add_argument("--log-level", default="INFO",
                        help="log level: DEBUG, INFO, WARNING, ERROR (default: INFO)")
    if with_verbose:
        parser.add_argument("-v", "--verbose", action="store_true",
                            help="DEBUG logging (overrides --log-level)")
    if with_dry_run:
        parser.add_argument("--dry-run", action="store_true",
                            help="log what would be done without doing it")
    return parser


def resolve_log_level(args, default="INFO"):
    """Effective log level for parsed args: DEBUG when --verbose was given."""
    if getattr(args, "verbose", False):
        return "DEBUG"
    return getattr(args, "log_level", default) or default
