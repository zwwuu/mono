"""Tests for common.config: validated JSON config shared by all apps."""

import json

import pytest

from common.config import (
    Config,
    ConfigError,
    check_required,
    load_json,
    redact,
    resolve_secret,
)


def write_json(tmp_path, data):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_load_json_returns_dict(tmp_path):
    path = write_json(tmp_path, {"a": {"b": 1}})
    data = load_json(path)
    assert data == {"a": {"b": 1}}


def test_load_json_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_json(str(tmp_path / "nope.json"))


def test_load_json_malformed(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_json(str(path))


def test_load_json_not_an_object(tmp_path):
    path = write_json(tmp_path, [1, 2, 3])
    with pytest.raises(ConfigError, match="JSON object"):
        load_json(str(path))


def test_check_required_ok_and_missing():
    check_required({"sftp": {"host": "h"}}, [("sftp", "host")])
    with pytest.raises(ConfigError, match="missing required 'sftp.host'"):
        check_required({"sftp": {}}, [("sftp", "host")])
    with pytest.raises(ConfigError, match="missing required 'nope.key'"):
        check_required({}, [("nope", "key")])


def test_resolve_secret_plain_and_env(monkeypatch):
    assert resolve_secret("plain", "sftp.password") == "plain"
    assert resolve_secret(None, "sftp.password") is None
    monkeypatch.setenv("MY_TEST_SECRET", "s3cr3t")
    assert resolve_secret({"env": "MY_TEST_SECRET"}, "sftp.password") == "s3cr3t"


def test_resolve_secret_missing_env():
    with pytest.raises(ConfigError, match="not set"):
        resolve_secret({"env": "DEFINITELY_NOT_SET_ZZZ"}, "sftp.password")


def test_resolve_secret_malformed():
    with pytest.raises(ConfigError, match="must be a string"):
        resolve_secret({"env": ""}, "sftp.password")
    with pytest.raises(ConfigError, match="must be a string"):
        resolve_secret({"other": "x"}, "sftp.password")


def test_config_dict_interface():
    cfg = Config({"sftp": {"host": "h"}, "retry": {}})
    assert cfg["sftp"]["host"] == "h"
    assert cfg.get("retry") == {}
    assert cfg.get("missing", "dflt") == "dflt"
    assert "sftp" in cfg
    assert "missing" not in cfg
    with pytest.raises(KeyError):
        cfg["missing"]


def test_redact_masks_secrets():
    data = {
        "sftp": {"host": "h", "password": "pw", "key_passphrase": "kp"},
        "email": {"password": "ep"},
        "other": {"password": "untouched"},
    }
    out = redact(data, (("sftp", ("password", "key_passphrase")), ("email", ("password",))))
    assert out["sftp"]["password"] == "***"
    assert out["sftp"]["key_passphrase"] == "***"
    assert out["email"]["password"] == "***"
    assert out["other"]["password"] == "untouched"  # not in secret paths
    assert data["sftp"]["password"] == "pw"  # original untouched


def test_config_redact_via_subclass():
    class MyConfig(Config):
        secret_paths = (("sftp", ("password",)),)

    cfg = MyConfig({"sftp": {"host": "h", "password": "pw"}})
    assert cfg.redact()["sftp"]["password"] == "***"
    assert cfg["sftp"]["password"] == "pw"
