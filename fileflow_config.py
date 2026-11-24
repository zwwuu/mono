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


def _drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


@dataclass
class SourceFile:
    directory: str
    name: str
    path: str  # directory + name (derived, not part of to_dict)
    optional: bool = False

    @classmethod
    def from_dict(cls, data: dict, transport: str, ctx: Optional[Context] = None,
                  where: str = "file") -> "SourceFile":
        ctx = ctx or Context()
        directory = ctx.path_for(transport, _require(data, "directory", where))
        name = ctx.render(_require(data, "name", where))
        join = os.path.join if transport == "local" else posixpath.join
        return cls(directory, name, join(directory, name), bool(data.get("optional", False)))

    def to_dict(self) -> dict:
        d = {"directory": self.directory, "name": self.name}
        if self.optional:
            d["optional"] = True
        return d


@dataclass
class Source:
    transport: str
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password_env: Optional[str] = None
    files: List[SourceFile] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict, ctx: Optional[Context] = None, where: str = "source") -> "Source":
        ctx = ctx or Context()
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

    def to_dict(self) -> dict:
        return _drop_none({
            "transport": self.transport,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "password_env": self.password_env,
            "files": [f.to_dict() for f in self.files],
        })

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
    def from_dict(cls, data: dict, ctx: Optional[Context] = None,
                  where: str = "destination") -> "Destination":
        ctx = ctx or Context()
        transport = _transport(data, where)
        host, port, username = _connection(data, transport, where)
        return cls(
            transport=transport,
            path=ctx.path_for(transport, _require(data, "path", where)),
            host=host,
            port=port,
            username=username,
        )

    def to_dict(self) -> dict:
        return _drop_none({
            "transport": self.transport,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "path": self.path,
        })


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

    def to_dict(self) -> dict:
        """Resolved config (placeholders filled, local paths absolute)."""
        return {
            "job": self.job,
            "category": self.category,
            "ledger_db": self.ledger_db,
            "staging_dir": self.staging_dir,
            "source": self.source.to_dict(),
            "destination": self.destination.to_dict(),
        }

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
    print(json.dumps(cfg.to_dict(), indent=2))
