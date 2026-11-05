"""Tests for common.app_config: app framework for linear CLI apps."""

import json
import logging
from pathlib import Path
from typing import ClassVar
from unittest import mock

import pytest

from common.app_config import AppConfig, Router


class SampleApp(AppConfig):
    APP_NAME = "test-app"
    VERSION = "0.1.0"
    DESCRIPTION = "test app"


def test_subclass_declaration_and_bootstrap():
    app = SampleApp(anchor=__file__, argv=[])
    assert app.APP_NAME == "test-app"
    assert app.VERSION == "0.1.0"
    assert app.CONFIG is None
    assert isinstance(app.ROUTER, Router)
    assert app.ROUTER.base == Path(__file__).resolve().parent
    assert app.MAILER is None
    assert app.LOGGER is not None
    assert app.args is not None


def test_missing_app_name_fails_loud():
    class Nameless(AppConfig):
        VERSION = "1.0.0"

    with pytest.raises(TypeError, match="must set APP_NAME"):
        Nameless(anchor=__file__, argv=[])


def test_missing_version_fails_loud():
    class Versionless(AppConfig):
        APP_NAME = "x"

    with pytest.raises(TypeError, match="must set VERSION"):
        Versionless(anchor=__file__, argv=[])


def test_run_not_implemented_on_base():
    with pytest.raises(NotImplementedError, match="must implement run"):
        SampleApp(anchor=__file__, argv=[]).run()


class FlagApp(SampleApp):
    WITH_VERBOSE = True

    def extra_args(self, parser):
        parser.add_argument("--target", default="x")


def test_extra_args_and_verbose_flag():
    app = FlagApp(anchor=__file__, argv=["--target", "y", "--verbose"])
    assert app.args.target == "y"
    assert app.LOGGER.getEffectiveLevel() == logging.DEBUG


class ConfigApp(SampleApp):
    WITH_CONFIG = True


def test_json_config_loaded(tmp_path):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"a": 1}), encoding="utf-8")
    app = ConfigApp(anchor=__file__, argv=["--config", str(cfg_file)])
    assert app.CONFIG == {"a": 1}


def test_missing_config_file_exits_2(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        ConfigApp(anchor=__file__, argv=["--config", str(tmp_path / "nope.json")])
    assert exc.value.code == 2
    assert "config error" in capsys.readouterr().err


class ValidatedApp(SampleApp):
    WITH_CONFIG = True

    def load_config(self):
        return {"validated": True}


def test_load_config_override():
    app = ValidatedApp(anchor=__file__, argv=[])
    assert app.CONFIG == {"validated": True}


class LifecycleApp(SampleApp):
    events: ClassVar[list] = []

    def setup(self):
        type(self).events.append("setup")

    def run(self):
        type(self).events.append("run")
        return 42

    def teardown(self):
        type(self).events.append("teardown")


def test_main_lifecycle_order_and_exit_code():
    LifecycleApp.events = []
    assert LifecycleApp.main(anchor=__file__, argv=[]) == 42
    assert LifecycleApp.events == ["setup", "run", "teardown"]


class FailingApp(SampleApp):
    events: ClassVar[list] = []

    def run(self):
        type(self).events.append("run")
        raise RuntimeError("boom")

    def teardown(self):
        type(self).events.append("teardown")


def test_main_unhandled_error_returns_1_and_still_teardowns():
    FailingApp.events = []
    assert FailingApp.main(anchor=__file__, argv=[]) == 1
    assert FailingApp.events == ["run", "teardown"]


class InterruptApp(SampleApp):
    def run(self):
        raise KeyboardInterrupt


def test_main_keyboard_interrupt_returns_130():
    assert InterruptApp.main(anchor=__file__, argv=[]) == 130


def test_router_resolves_next_to_exe_when_frozen(tmp_path):
    with mock.patch("common.app_config.app_config.is_frozen", return_value=True), mock.patch(
        "common.app_config.app_config.exe_dir", return_value=str(tmp_path)
    ):
        app = SampleApp(anchor=__file__, argv=[])
    assert app.ROUTER.base == tmp_path


def test_router_truediv_and_join(tmp_path):
    router = Router(base=tmp_path)
    assert router / "a" == tmp_path / "a"
    assert router.join("a", "b") == tmp_path / "a" / "b"
