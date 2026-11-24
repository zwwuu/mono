"""Parser for the fileflow job config. Standard library only."""
import json
import os
import posixpath
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import List, Optional

TRANSPORTS = {"ftp", "sftp"}


class ConfigError(Exception):
    pass


class Context:
    """App context: fills {placeholders} and anchors relative paths."""

    def __init__(self, when: Optional[date] = None, base_dir: Optional[str] = None, **extra: str):
        when = when or date.today()
        self.values = {
            "year": f"{when.year:04d}",
            "month": f"{when.month:02d}",
            "day": f"{when.day:02d}",
        }
        self.values.update(extra)  # e.g. category="sales"
        # relative paths are resolved against the program's directory
        self.base_dir = os.path.abspath(base_dir or os.path.dirname(os.path.abspath(sys.argv[0])))

    def render(self, template: str) -> str:
        try:
            return template.format_map(self.values)
        except KeyError as e:
            raise ConfigError(f"Unknown placeholder {e} in {template!r}") from None

    def local_path(self, template: str) -> str:
        """Fill placeholders, then return an absolute path on this machine."""
        return os.path.normpath(os.path.join(self.base_dir, self.render(template)))

    def remote_path(self, template: str) -> str:
        """Fill placeholders, then return an absolute posix path (ftp/sftp)."""
        base = Path(self.base_dir).as_posix()
        return posixpath.normpath(posixpath.join(base, self.render(template)))


def _require(data: dict, key: str, where: str):
    if key not in data:
        raise ConfigError(f"Missing '{key}' in {where}")
    return data[key]


def _transport(data: dict, where: str) -> str:
    transport = _require(data, "transport", where)
    if transport not in TRANSPORTS:
        raise ConfigError(f"{where}: unsupported transport {transport!r}")
    return transport


@dataclass
class SourceFile:
    directory: str  # absolute, remote
    name: str
    optional: bool = False

    @property
    def path(self) -> str:
        return posixpath.join(self.directory, self.name)

    @classmethod
    def from_dict(cls, data: dict, ctx: Context, where: str) -> "SourceFile":
        return cls(
            directory=ctx.remote_path(_require(data, "directory", where)),
            name=ctx.render(_require(data, "name", where)),
            optional=bool(data.get("optional", False)),
        )


@dataclass
class Source:
    transport: str
    host: str
    port: int
    username: str
    password_env: Optional[str] = None
    files: List[SourceFile] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict, ctx: Context, where: str = "source") -> "Source":
        return cls(
            transport=_transport(data, where),
            host=_require(data, "host", where),
            port=int(_require(data, "port", where)),
            username=_require(data, "username", where),
            password_env=data.get("password_env"),
            files=[
                SourceFile.from_dict(f, ctx, f"{where}.files[{i}]")
                for i, f in enumerate(_require(data, "files", where))
            ],
        )

    def password(self) -> Optional[str]:
        """Read the password from the environment only when needed."""
        if not self.password_env:
            return None
        value = os.environ.get(self.password_env)
        if value is None:
            raise ConfigError(f"Environment variable {self.password_env} is not set")
        return value


@dataclass
class Destination:
    transport: str
    host: str
    port: int
    username: str
    path: str  # absolute, remote

    @classmethod
    def from_dict(cls, data: dict, ctx: Context, where: str = "destination") -> "Destination":
        return cls(
            transport=_transport(data, where),
            host=_require(data, "host", where),
            port=int(_require(data, "port", where)),
            username=_require(data, "username", where),
            path=ctx.remote_path(_require(data, "path", where)),
        )


@dataclass
class JobConfig:
    job: str
    category: str
    ledger_db: str    # absolute, local
    staging_dir: str  # absolute, local
    source: Source
    destination: Destination

    @classmethod
    def from_dict(cls, data: dict, ctx: Optional[Context] = None) -> "JobConfig":
        ctx = ctx or Context()
        return cls(
            job=_require(data, "job", "config"),
            category=_require(data, "category", "config"),
            ledger_db=ctx.local_path(_require(data, "ledger_db", "config")),
            staging_dir=ctx.local_path(_require(data, "staging_dir", "config")),
            source=Source.from_dict(_require(data, "source", "config"), ctx),
            destination=Destination.from_dict(_require(data, "destination", "config"), ctx),
        )

    @classmethod
    def load(cls, path: str, ctx: Optional[Context] = None) -> "JobConfig":
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise ConfigError(f"Cannot read {path}: {e}") from None
        return cls.from_dict(data, ctx)


if __name__ == "__main__":
    ctx = Context(category=sys.argv[2] if len(sys.argv) > 2 else "sales")
    cfg = JobConfig.load(sys.argv[1] if len(sys.argv) > 1 else "config.json", ctx)
    d, s = cfg.destination, cfg.source
    print(f"job: {cfg.job} ({cfg.category})")
    print(f"ledger_db: {cfg.ledger_db}")
    print(f"staging_dir: {cfg.staging_dir}")
    print(f"destination: {d.transport}://{d.host}:{d.port}{d.path}")
    print(f"source: {s.transport}://{s.host}:{s.port}")
    for f in s.files:
        print(f"  {'(optional) ' if f.optional else ''}{f.path}")
