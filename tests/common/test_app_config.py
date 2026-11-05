"""Tests for common.app_config: bootstrap template for linear apps."""

import json
import logging
from pathlib import Path
from unittest import mock

import pytest

from common.app_config import AppConfig, Router


class SampleApp(AppConfig):
    APP_NAME = "test-app"
    VERSION = "0.1.0"
    DESCRIPTION = "test app"


def make_cfg(cls=SampleApp, **kwargs):
    kwargs.setdefault("anchor", __file__)
    kwargs.setdefault("argv", [])
    return cls(**kwargs)


def test_subclass_declaration_and_defaults():
    cfg = make_cfg()
    assert cfg.APP_NAME == "test-app"
    assert cfg.VERSION == "0.1.0"
    assert cfg.CONFIG is None
    assert isinstance(cfg.ROUTER, Router)
    assert cfg.MAILER is None
    assert cfg.LOGGER is not None
    assert cfg.ROUTER.base == Path(__file__).resolve().parent
    assert cfg.args is not None


def test_missing_app_name_fails_loud():
    class Nameless(AppConfig):
        VERSION = "1.0.0"

    with pytest.raises(TypeError, match="must set APP_NAME"):
        make_cfg(cls=Nameless)


def test_missing_version_fails_loud():
    class Versionless(AppConfig):
        APP_NAME = "x"

    with pytest.raises(TypeError, match="must set VERSION"):
        make_cfg(cls=Versionless)


def test_extra_args():
    def extra(parser):
        parser.add_argument("--target", default="x")

    cfg = make_cfg(extra_args=extra, argv=["--target", "y"])
    assert cfg.args.target == "y"


def test_json_config_loaded(tmp_path):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"a": 1}), encoding="utf-8")
    cfg = make_cfg(with_config=True, argv=["--config", str(cfg_file)])
    assert cfg.CONFIG == {"a": 1}


def test_missing_config_file_exits_2(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        make_cfg(with_config=True, argv=["--config", str(tmp_path / "nope.json")])
    assert exc.value.code == 2
    assert "config error" in capsys.readouterr().err


def test_load_config_callable_receives_cfg():
    seen = {}

    def load(cfg):
        seen["cfg"] = cfg
        return {"validated": True}

    cfg = make_cfg(with_config=True, load_config=load)
    assert cfg.CONFIG == {"validated": True}
    assert seen["cfg"] is cfg


def test_verbose_forces_debug_logging():
    cfg = make_cfg(with_verbose=True, argv=["--verbose"])
    assert cfg.LOGGER.getEffectiveLevel() == logging.DEBUG


def test_paths_resolve_next_to_exe_when_frozen(tmp_path):
    with mock.patch("common.app_config.app_config.is_frozen", return_value=True), mock.patch(
        "common.app_config.app_config.exe_dir", return_value=str(tmp_path)
    ):
        cfg = make_cfg()
    assert cfg.ROUTER.base == tmp_path


def test_router_truediv_and_join(tmp_path):
    router = Router(base=tmp_path)
    assert router / "a" == tmp_path / "a"
    assert router.join("a", "b") == tmp_path / "a" / "b"
