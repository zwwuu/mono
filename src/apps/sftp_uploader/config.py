"""Load and validate config.json for sftp-uploader.

Generic machinery (Config, ConfigError, secret resolution) lives in
common.config; the required keys, defaults, and value checks below
are sftp-uploader's own.
"""
from __future__ import annotations

from common.config import (
    Config as _BaseConfig,
)
from common.config import (
    ConfigError,
    check_required,
    load_json,
    resolve_secret,
)
from common.config import (
    redact as _redact,
)

__all__ = [
    "REQUIRED",
    "SECRET_PATHS",
    "VALID_HOST_KEY_POLICIES",
    "VALID_ON_EXISTING",
    "VALID_PICKS",
    "Config",
    "ConfigError",
    "load_config",
    "redact",
]


class Config(_BaseConfig):
    """sftp-uploader's config: common's dict-like wrapper plus redact()."""

    secret_paths = (
        ("sftp", ("password", "key_passphrase")),
        ("email", ("password",)),
    )

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

# (section, (key, ...)) pairs holding secrets, masked by redact().
# (Also Config.secret_paths, so config.redact() works.)
SECRET_PATHS = Config.secret_paths


def load_config(path: str) -> Config:
    """Read *path*, validate it, fill defaults, resolve secrets. Raises ConfigError."""
    data = load_json(path)
    check_required(data, REQUIRED)

    # --- defaults ---------------------------------------------------------
    scfg = data.setdefault("sftp", {})
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
        raise ConfigError(
            f"config.json: sftp.host_key_policy must be one of {VALID_HOST_KEY_POLICIES}"
        )
    if scfg["on_existing"] not in VALID_ON_EXISTING:
        raise ConfigError(
            f"config.json: sftp.on_existing must be one of {VALID_ON_EXISTING}"
        )
    if not scfg.get("key_file") and not scfg.get("password"):
        raise ConfigError("config.json: sftp needs either key_file or password for auth")

    fcfg = data.setdefault("file", {})
    fcfg.setdefault("pick", "newest")
    if fcfg["pick"] not in VALID_PICKS:
        raise ConfigError(f"config.json: file.pick must be one of {VALID_PICKS}")

    data.setdefault("verify", True)

    rcfg = data.setdefault("retry", {})
    rcfg.setdefault("max_attempts", 3)
    rcfg.setdefault("backoff_seconds", 10)
    rcfg.setdefault("backoff_max_seconds", 300)
    if int(rcfg["max_attempts"]) < 1:
        raise ConfigError("config.json: retry.max_attempts must be >= 1")

    ecfg = data.setdefault("email", {})
    ecfg.setdefault("enabled", True)
    ecfg.setdefault("smtp_port", 587)
    ecfg.setdefault("use_tls", True)
    ecfg.setdefault("subject_prefix", "[sftp-uploader]")
    if ecfg["enabled"]:
        for key in ("smtp_host", "from", "to"):
            if not ecfg.get(key):
                raise ConfigError(
                    f"config.json: email is enabled but email.{key} is missing"
                )
        if not isinstance(ecfg["to"], list):
            raise ConfigError("config.json: email.to must be a list of addresses")

    data.setdefault("logging", {})

    # --- secrets ----------------------------------------------------------
    for where in ("sftp.password", "sftp.key_passphrase", "email.password"):
        section, key = where.split(".")
        val = data.get(section, {}).get(key)
        if val is not None:
            data[section][key] = resolve_secret(val, where)

    return Config(data)


def redact(cfg: dict) -> dict:
    """Return a deep copy of the config with secret values masked (safe to log)."""
    return _redact(cfg, SECRET_PATHS)
