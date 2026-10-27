"""Command line: parse arguments, run the check, return the exit code.

Stdout carries one short result line on the success paths; errors
go to stderr with the reason. --quiet suppresses the stdout line —
the exit code is the result either way. Usage errors exit 2 via
argparse, matching ExitCode.USAGE.
"""

import argparse
import logging
import sys

from . import APP_NAME, __version__
from .checker import DEFAULT_FIELDS, FormChecker
from .errors import FormCheckError
from .exit_codes import ExitCode
from .matcher import YesMatcher


def build_parser():
    """The argument parser for the CLI."""
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description="Check a PDF form: exit 1 when field A1 or A2 is 'yes'.",
    )
    parser.add_argument("pdf", help="path to the PDF form")
    parser.add_argument(
        "--fields",
        nargs="+",
        default=list(DEFAULT_FIELDS),
        metavar="NAME",
        help="field names to check (default: A1 A2)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print nothing on success; the exit code is the result",
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    return parser


def format_result(result, matcher):
    """One stable line: MATCH with the yes fields, or NO_MATCH with values."""
    if result.matched:
        parts = ", ".join(
            f"{name}={matcher.normalize(result.values[name])}" for name in result.matched_fields
        )
        return f"MATCH: {parts}"
    parts = ", ".join(
        f"{name}={matcher.normalize(value) or 'empty'}" for name, value in result.values.items()
    )
    return f"NO_MATCH: {parts}"


def main(argv=None):
    """Run the check; return the exit code (never raises for bad input)."""
    # pypdf logs parse warnings to stderr; the caller gets one error
    # line from us instead, so silence everything below ERROR here.
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    args = build_parser().parse_args(argv)
    matcher = YesMatcher()
    checker = FormChecker(fields=args.fields, matcher=matcher)
    try:
        result = checker.check(args.pdf)
    except FormCheckError as exc:
        print(f"{APP_NAME}: error: {exc}", file=sys.stderr)
        return int(exc.exit_code)
    if not args.quiet:
        print(format_result(result, matcher))
    return int(ExitCode.MATCH if result.matched else ExitCode.NO_MATCH)
