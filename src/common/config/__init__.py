"""Validated JSON config shared by all apps (stdlib only).

Usage:
    from common.config import (
        Config, ConfigError, check_required, load_json, redact, resolve_secret,
    )

    data = load_json("config.json")   # raw dict, raises ConfigError
    check_required(data, [("sftp", "host"), ("file", "pattern")])
    data["sftp"]["password"] = resolve_secret(data["sftp"].get("password"),
                                              "sftp.password")
    cfg = Config(data)
    cfg["sftp"]["host"]
    cfg.get("retry", {})

App-specific validation (defaults, value checks) lives in the app:
load the dict, validate it, then wrap it in Config.
"""

import copy
import json
import os

__all__ = [
    "Config",
    "ConfigError",
    "check_required",
    "load_json",
    "redact",
    "resolve_secret",
]


class ConfigError(Exception):
    """Raised when a config file is missing, malformed, or invalid."""


class Config:
    """Validated configuration. Behaves like a read-mostly dict, so
    ``config["sftp"]`` and ``config.get("retry", {})`` both work.

    Subclasses set ``secret_paths`` to enable ``redact()``:
        class MyConfig(Config):
            secret_paths = (("sftp", ("password",)),)
    """

    secret_paths = ()

    def __init__(self, data):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)

    def __contains__(self, key):
        return key in self._data

    def __repr__(self):
        return f"{type(self).__name__}({self._data!r})"

    def redact(self):
        """Copy of the config with secret values masked (safe to log)."""
        return redact(self._data, self.secret_paths)


def load_json(path):
    """Load a JSON config file as a dict. Raises ConfigError when the
    file is missing, unreadable, malformed, or not a JSON object."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}")
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config file is not valid JSON ({path}): {exc}")
    except OSError as exc:
        raise ConfigError(f"config file cannot be read ({path}): {exc}")
    if not isinstance(data, dict):
        raise ConfigError("config file must contain a JSON object at the top level")
    return data


def check_required(data, required):
    """Raise ConfigError unless every (section, key) pair is present
    and non-empty, e.g. check_required(data, [("sftp", "host")])."""
    for section, key in required:
        if not isinstance(data.get(section), dict) or not data[section].get(key):
            raise ConfigError(f"config: missing required '{section}.{key}'")


def resolve_secret(value, where):
    """Resolve a secret value: a plain string, or {"env": "VAR_NAME"}
    to read it from an environment variable. Raises ConfigError when
    the env var is missing or the mapping is malformed."""
    if isinstance(value, dict):
        if set(value) != {"env"} or not value["env"]:
            raise ConfigError(f'config: {where} must be a string or {{"env": "VAR"}}')
        env_val = os.environ.get(value["env"])
        if not env_val:
            raise ConfigError(
                f"config: {where} refers to env var {value['env']!r}, which is not set"
            )
        return env_val
    return value


def env_secret(name):
    """Secrets come from environment variables, never from the config file."""
    return os.environ.get(name, "")


def redact(data, secret_paths):
    """Deep copy of the config with secret values masked (safe to log).

    secret_paths: ((section, (key, ...)), ...) e.g.
    redact(data, (("sftp", ("password", "key_passphrase")),
                  ("email", ("password",)))).
    """
    out = copy.deepcopy(data)
    for section, keys in secret_paths:
        if isinstance(out.get(section), dict):
            for key in keys:
                if out[section].get(key):
                    out[section][key] = "***"
    return out
