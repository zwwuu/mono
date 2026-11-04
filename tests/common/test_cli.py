"""Tests for common.cli: the standard argparse flags every app shares."""

import pytest

from common.cli import build_parser, resolve_log_level


def test_version_flag(capsys):
    parser = build_parser("my-app", "Does things.", "1.2.3")
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == "my-app 1.2.3"


def test_defaults():
    args = build_parser("my-app", "Does things.", "1.2.3").parse_args([])
    assert args.log_level == "INFO"
    assert not hasattr(args, "config")
    assert not hasattr(args, "verbose")
    assert not hasattr(args, "dry_run")


def test_config_default():
    args = build_parser("my-app", "Does things.", "1.2.3",
                        with_config=True).parse_args([])
    assert args.config == "config.json"
    args = build_parser("my-app", "Does things.", "1.2.3", with_config=True,
                        config_default="app.json").parse_args([])
    assert args.config == "app.json"


def test_opt_in_flags():
    parser = build_parser(
        "my-app", "Does things.", "1.2.3",
        with_config=True, with_dry_run=True, with_verbose=True,
    )
    args = parser.parse_args(["--config", "x.json", "--dry-run", "-v"])
    assert args.config == "x.json"
    assert args.dry_run is True
    assert args.verbose is True


def test_app_can_add_own_flags():
    parser = build_parser("my-app", "Does things.", "1.2.3")
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(["--host", "example.com"])
    assert args.host == "example.com"
    assert args.log_level == "INFO"  # standard flags still there


def test_resolve_log_level():
    parser = build_parser("my-app", "Does things.", "1.2.3", with_verbose=True)
    assert resolve_log_level(parser.parse_args([])) == "INFO"
    assert resolve_log_level(parser.parse_args(["--log-level", "warning"])) == "warning"
    assert resolve_log_level(parser.parse_args(["-v"])) == "DEBUG"
    assert (
        resolve_log_level(parser.parse_args(["--log-level", "ERROR", "-v"])) == "DEBUG"
    )


def test_resolve_log_level_without_verbose_flag():
    parser = build_parser("my-app", "Does things.", "1.2.3")
    assert resolve_log_level(parser.parse_args([])) == "INFO"
