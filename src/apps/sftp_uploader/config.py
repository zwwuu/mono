"""Load and validate config.json.

Secrets (sftp.password, sftp.key_passphrase, email.password) may be a plain
string or ``{"env": "VAR_NAME"}`` to read the value from an environment variable.
"""
from __future__ import annotations

import copy
import json
import os

# (section, key) pairs that must be present and non-empty.
REQUIRED = [
    ("sftp", "host"),
    ("sftp", "username"),
    ("file", "local_dir"),
    ("file", "pattern"),
]

VALID_PICKS = ("newest", "oldest", "first")
VALID_HOST_KEY_POLICIES = ("auto_add", "reject")
VALID_ON_EXISTING = ("overwrite", "skip", "fail")


class ConfigError(Exception):
    """Raised when config.json is missing, malformed, or incomplete."""


class Config:
    """Validated configuration. Behaves like a read-mostly dict, so
    ``config["sftp"]`` and ``config.get("retry", {})`` both work."""

    def __init__(self, data: dict):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)

    def __contains__(self, key):
        return key in self._data

    def redact(self) -> dict:
        """Copy of the config with secret values masked (safe to log)."""
        return redact(self._data)


def _resolve_secret(value, where: str):
    if isinstance(value, dict):
        if set(value) != {"env"} or not value["env"]:
            raise ConfigError(f"config.json: {where} must be a string or {{\"env\": \"VAR\"}}")
        env_val = os.environ.get(value["env"])
        if not env_val:
            raise ConfigError(f"config.json: {where} refers to env var {value['env']!r}, which is not set")
        return env_val
    return value


def load_config(path: str) -> Config:
    """Read *path*, validate it, fill defaults, resolve secrets. Raises ConfigError."""
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}")
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config file is not valid JSON ({path}): {exc}")
    if not isinstance(cfg, dict):
        raise ConfigError("config file must contain a JSON object at the top level")

    for section, key in REQUIRED:
        if not isinstance(cfg.get(section), dict) or not cfg[section].get(key):
            raise ConfigError(f"config.json: missing required '{section}.{key}'")

    # --- defaults ---------------------------------------------------------
    scfg = cfg.setdefault("sftp", {})
    scfg.setdefault("port", 22)
    scfg.setdefault("password", None)
    scfg.setdefault("key_file", None)
    scfg.setdefault("key_passphrase", None)
    scfg.setdefault("host_key_policy", "auto_add")
    scfg.setdefault("timeout", 30)
    scfg.setdefault("remote_dir", "")
    scfg.setdefault("create_remote_dir", True)
    scfg.setdefault("on_existing", "overwrite")
    if scfg["host_key_policy"] not in VALID_HOST_KEY_POLICIES:
        raise ConfigError(f"config.json: sftp.host_key_policy must be one of {VALID_HOST_KEY_POLICIES}")
    if scfg["on_existing"] not in VALID_ON_EXISTING:
        raise ConfigError(f"config.json: sftp.on_existing must be one of {VALID_ON_EXISTING}")
    if not scfg.get("key_file") and not scfg.get("password"):
        raise ConfigError("config.json: sftp needs either key_file or password for auth")

    fcfg = cfg.setdefault("file", {})
    fcfg.setdefault("pick", "newest")
    if fcfg["pick"] not in VALID_PICKS:
        raise ConfigError(f"config.json: file.pick must be one of {VALID_PICKS}")

    cfg.setdefault("verify", True)

    rcfg = cfg.setdefault("retry", {})
    rcfg.setdefault("max_attempts", 3)
    rcfg.setdefault("backoff_seconds", 10)
    rcfg.setdefault("backoff_max_seconds", 300)
    if int(rcfg["max_attempts"]) < 1:
        raise ConfigError("config.json: retry.max_attempts must be >= 1")

    ecfg = cfg.setdefault("email", {})
    ecfg.setdefault("enabled", True)
    ecfg.setdefault("smtp_port", 587)
    ecfg.setdefault("use_tls", True)
    ecfg.setdefault("subject_prefix", "[sftp-uploader]")
    if ecfg["enabled"]:
        for key in ("smtp_host", "from", "to"):
            if not ecfg.get(key):
                raise ConfigError(f"config.json: email is enabled but email.{key} is missing")
        if not isinstance(ecfg["to"], list):
            raise ConfigError("config.json: email.to must be a list of addresses")

    cfg.setdefault("logging", {})

    # --- secrets ----------------------------------------------------------
    for where in ("sftp.password", "sftp.key_passphrase", "email.password"):
        section, key = where.split(".")
        val = cfg.get(section, {}).get(key)
        if val is not None:
            cfg[section][key] = _resolve_secret(val, where)

    return Config(cfg)


def redact(cfg: dict) -> dict:
    """Return a deep copy of the config with secret values masked (safe to log)."""
    out = copy.deepcopy(cfg)
    for section, keys in (("sftp", ("password", "key_passphrase")), ("email", ("password",))):
        if isinstance(out.get(section), dict):
            for key in keys:
                if out[section].get(key):
                    out[section][key] = "***"
    return out
