"""Parser for the fileflow job config. Standard library only."""
import json
import os
import posixpath
import sys
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

TRANSPORTS = {"local", "ftp", "sftp"}


class ConfigError(Exception):
    pass


class Context:
    """App context: fills {placeholders} and anchors relative local paths."""

    def __init__(self, base_dir: Optional[str] = None, **values: str):
        # placeholder values come only from the app, e.g. Context(year="2026", category="sales")
        self.values = dict(values)
        # relative local paths are resolved against the program's directory
        self.base_dir = os.path.abspath(base_dir or os.path.dirname(os.path.abspath(sys.argv[0])))

    def render(self, template: str) -> str:
        try:
            return template.format_map(self.values)
        except KeyError as e:
            raise ConfigError(f"Unknown placeholder {e} in {template!r}") from None

    def local_path(self, template: str) -> str:
        """Fill placeholders, then return an absolute, cleaned local path."""
        return os.path.normpath(os.path.join(self.base_dir, self.render(template)))

    def path_for(self, transport: str, template: str) -> str:
        """Local: resolve to absolute. ftp/sftp: fill placeholders only."""
        if transport == "local":
            return self.local_path(template)
        return self.render(template)


def _require(data: dict, key: str, where: str):
    if key not in data:
        raise ConfigError(f"Missing '{key}' in {where}")
    return data[key]


def _transport(data: dict, where: str) -> str:
    transport = _require(data, "transport", where)
    if transport not in TRANSPORTS:
        raise ConfigError(f"{where}: unsupported transport {transport!r}")
    return transport


def _connection(data: dict, transport: str, where: str) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    """host/port/username: required for ftp/sftp, ignored for local."""
    if transport == "local":
        return None, None, None
    return (
        _require(data, "host", where),
        int(_require(data, "port", where)),
        _require(data, "username", where),
    )


@dataclass
class SourceFile:
    directory: str
    name: str
    path: str  # directory + name
    optional: bool = False

    @classmethod
    def from_dict(cls, data: dict, transport: str, ctx: Context, where: str) -> "SourceFile":
        directory = ctx.path_for(transport, _require(data, "directory", where))
        name = ctx.render(_require(data, "name", where))
        join = os.path.join if transport == "local" else posixpath.join
        return cls(directory, name, join(directory, name), bool(data.get("optional", False)))


@dataclass
class Source:
    transport: str
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password_env: Optional[str] = None
    files: List[SourceFile] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict, ctx: Context, where: str = "source") -> "Source":
        transport = _transport(data, where)
        host, port, username = _connection(data, transport, where)
        return cls(
            transport=transport,
            host=host,
            port=port,
            username=username,
            password_env=data.get("password_env"),
            files=[
                SourceFile.from_dict(f, transport, ctx, f"{where}.files[{i}]")
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
    path: str
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict, ctx: Context, where: str = "destination") -> "Destination":
        transport = _transport(data, where)
        host, port, username = _connection(data, transport, where)
        return cls(
            transport=transport,
            path=ctx.path_for(transport, _require(data, "path", where)),
            host=host,
            port=port,
            username=username,
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
    from datetime import date  # demo only

    today = date.today()
    ctx = Context(
        year=f"{today.year:04d}",
        month=f"{today.month:02d}",
        day=f"{today.day:02d}",
        category=sys.argv[2] if len(sys.argv) > 2 else "sales",
    )
    cfg = JobConfig.load(sys.argv[1] if len(sys.argv) > 1 else "config.json", ctx)
    d, s = cfg.destination, cfg.source
    print(f"job: {cfg.job} ({cfg.category})")
    print(f"ledger_db: {cfg.ledger_db}")
    print(f"staging_dir: {cfg.staging_dir}")
    print(f"destination: {d.transport} {d.host or ''} {d.path}")
    print(f"source: {s.transport} {s.host or ''}")
    for f in s.files:
        print(f"  {'(optional) ' if f.optional else ''}{f.path}")
