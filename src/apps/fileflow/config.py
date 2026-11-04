# FileFlow — JSON configuration loading.
#
# Config files are plain JSON objects with one key per section:
# app, demo, ftp, smb, smtp, notify, steps, scheduler, source
# (source only carries a download timeout). Tasks live in tasks.json,
# not here. See config.example.json for the full template.
# Secrets (passwords) never live in the config file — they come from
# environment variables (FILEFLOW_FTP_PASSWORD, FILEFLOW_SMB_PASSWORD,
# FILEFLOW_SMTP_PASSWORD). Config file resolution: --config flag, then
# $FILEFLOW_CONFIG, then ./config.json.
import json
import os
from dataclasses import dataclass, field

DATA_SUBDIRS = (
    "work",
    "archive",
    "outbox",
    "demo_ftp/inbox",
    "demo_ftp/processed",
    "demo_smb",
    "demo_source",
)


@dataclass
class Config:
    raw: dict = field(default_factory=dict)
    source_path: str = ""

    def get(self, section, key, default=None):
        return self.raw.get(section, {}).get(key, default)

    # ---- typed helpers -------------------------------------------------
    @property
    def data_dir(self):
        return os.path.abspath(self.get("app", "data_dir", "./data"))

    def path(self, *parts):
        return os.path.join(self.data_dir, *parts)


def default_config_path():
    return os.environ.get("FILEFLOW_CONFIG", "./config.json")


def load_config(path=None):
    """Load a JSON config file, creating the data directories."""
    path = path or default_config_path()
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"config file {path} must contain a JSON object")
    cfg = Config(raw=raw, source_path=os.path.abspath(path))
    os.makedirs(cfg.data_dir, exist_ok=True)
    for sub in DATA_SUBDIRS:
        os.makedirs(cfg.path(sub), exist_ok=True)
    return cfg


def env_secret(name):
    """Secrets come from environment variables, never from the config file."""
    return os.environ.get(name, "")
