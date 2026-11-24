"""Infrastructure -- config parser: JSON dict -> typed config.

One builder reads the dataclasses themselves: ``_build`` turns a
dict into a dataclass from its fields and type hints, so the shape
of the config is defined once, in the dataclasses. Anything wrong
raises ``ConfigError`` naming the FIRST problem found (missing keys,
wrong types, unknown keys, unknown source/transport kinds, unknown
template tokens, out-of-range values) instead of failing halfway
through a run, after files were already downloaded, with a bare
KeyError. ``load_config`` reads a file and parses it;
``--check-config`` runs just this step and exits.

The parser checks shape only. It does not check that a named env
var is set -- the env may be set on the deployment machine, not
the machine running the check -- and it does not connect to
anything. Secrets still resolve at build time, env-first with a
literal fallback, as before.

One config is one job. Templates render against the job's own
context -- its ``variables`` plus {date}/{time} in the job's own
formats -- so a typo fails at startup but a future app's own
tokens are never 'unknown'.
"""

from __future__ import annotations

import json
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from pathlib import Path
from types import UnionType
from typing import ClassVar, Union, get_args, get_origin, get_type_hints

from ..domain.integrity import render_name, render_template, template_context
from ..domain.procedures import get_procedure
from .playwright_source import PlaywrightSourceError
from .playwright_source import validate_actions as validate_playwright_actions
from .selenium_source import SeleniumSourceError, validate_actions

# JSON key -> dataclass field, where they differ ("transport" is the
# kind selector; "from" is a Python keyword).
_FIELD_KEY = {"kind": "transport", "from_addr": "from"}
# Keys a transport carries, inline in a destination / remote source.
_TRANSPORT_KEYS = {
    "transport",
    "host",
    "port",
    "username",
    "password_env",
    "password",
    "key_file_env",
    "key_file",
    "root",
}
# Field -> (min, max); None means unbounded.
_RANGES = {
    "port": (1, 65535),
    "smtp_port": (1, 65535),
    "timeout_seconds": (1, None),
    "min_age_seconds": (0, None),
}
# Every token the framework itself provides, across directory
# templates and rename patterns. A job variable colliding with
# one of these is a config error -- the built-in always wins.
TEMPLATE_BUILTINS = {
    "year",
    "month",
    "day",
    "date",
    "time",
    "job",
    "category",
    "name",
    "ext",
    "run_id",
}


class ConfigError(ValueError):
    """The config file is invalid; str() names the first problem found."""


# -- typed config -------------------------------------------------------


@dataclass(frozen=True)
class TransportConfig:
    """One transport, wherever it appears: destination or remote source."""

    kind: str = "local"
    host: str = ""
    port: int = 21
    username: str = ""
    password_env: str | None = None
    password: str | None = None
    key_file_env: str | None = None
    key_file: str | None = None
    root: str = ""


@dataclass(frozen=True)
class RemoteFile:
    directory: str
    name: str
    optional: bool = False


@dataclass(frozen=True)
class WebsiteSourceConfig:
    type: ClassVar[str] = "website"
    url: str = ""
    timeout_seconds: int = 60


@dataclass(frozen=True)
class LocalSourceConfig:
    type: ClassVar[str] = "local"
    path: str = ""


@dataclass(frozen=True)
class LocalDirSourceConfig:
    type: ClassVar[str] = "local-dir"
    directory: str = ""
    pattern: str = "*"
    min_age_seconds: float = 2


@dataclass(frozen=True)
class RemoteDirSourceConfig:
    type: ClassVar[str] = "remote-dir"
    transport: TransportConfig = TransportConfig()
    remote_dir: str = ""
    pattern: str = "*"
    min_age_seconds: float = 5


@dataclass(frozen=True)
class RemoteFilesSourceConfig:
    type: ClassVar[str] = "remote-files"
    transport: TransportConfig = TransportConfig()
    files: tuple[RemoteFile, ...] = ()
    min_age_seconds: float = 5


@dataclass(frozen=True)
class SmbSourceConfig:
    type: ClassVar[str] = "smb"
    server: str = ""
    share: str = ""
    path: str = ""
    pattern: str = "*"
    min_age_seconds: float = 5
    username: str = ""
    password_env: str | None = None


@dataclass(frozen=True)
class SeleniumSourceConfig:
    type: ClassVar[str] = "selenium"
    actions: tuple[dict, ...] = ()
    headless: bool = True
    timeout_seconds: int = 60
    executable_path: str | None = None


@dataclass(frozen=True)
class PlaywrightSourceConfig:
    type: ClassVar[str] = "playwright"
    actions: tuple[dict, ...] = ()
    headless: bool = True
    timeout_seconds: int = 60
    executable_path: str | None = None


SourceConfig = (
    WebsiteSourceConfig
    | LocalSourceConfig
    | LocalDirSourceConfig
    | RemoteDirSourceConfig
    | RemoteFilesSourceConfig
    | SmbSourceConfig
    | SeleniumSourceConfig
    | PlaywrightSourceConfig
)


@dataclass(frozen=True)
class DestinationConfig:
    transport: TransportConfig = TransportConfig()
    directory: str | None = None


@dataclass(frozen=True)
class DeliverConfig:
    upload_url: str = ""
    form_field: str = "file"
    auth_env: str | None = None
    timeout_seconds: int = 60


@dataclass(frozen=True)
class NotifyConfig:
    """One email per completed run. ``from_addr`` is the config key
    ``"from"`` (a Python keyword) and the SMTP login username."""

    smtp_host: str = ""
    smtp_port: int = 587
    from_addr: str = ""
    to: str = ""
    password_env: str | None = None


@dataclass(frozen=True)
class AgentConfig:
    """One config is one job. ``source`` is None for a pickup-only
    config; ``deliver`` / ``notify`` / ``log_file`` are None when
    the config has no pickup section / sends no email / logs to
    the console only."""

    job: str
    staging_dir: str
    ledger_db: str
    destination: DestinationConfig
    source: SourceConfig | None = None
    category: str | None = None
    variables: dict[str, str] = field(default_factory=dict)
    date_format: str = "%Y%m%d"
    time_format: str = "%H%M%S"
    procedure: str = "rename-only"
    expect_csv: bool = False
    rename_pattern: str = "{date}_{name}{ext}"
    deliver: DeliverConfig | None = None
    procedures_module: str | None = None
    notify: NotifyConfig | None = None
    log_file: str | None = None


# Fields that must be present and non-empty even though the dataclass
# gives them a "" default (so direct construction still works).
_REQUIRED = {
    (WebsiteSourceConfig, "url"),
    (LocalSourceConfig, "path"),
    (LocalDirSourceConfig, "directory"),
    (RemoteDirSourceConfig, "remote_dir"),
    (SmbSourceConfig, "server"),
    (SmbSourceConfig, "share"),
    (DeliverConfig, "upload_url"),
    (NotifyConfig, "smtp_host"),
    (NotifyConfig, "from_addr"),
    (NotifyConfig, "to"),
}


def source_reference(source: SourceConfig) -> str | None:
    """The manifest's ``source_url``: what the old code read from
    the raw source dict (``url``, ``path``, or ``directory`` -- in that
    order). Remote, selenium/playwright and smb-with-no-path sources have none."""
    if isinstance(source, WebsiteSourceConfig):
        return source.url
    if isinstance(source, LocalSourceConfig):
        return source.path
    if isinstance(source, LocalDirSourceConfig):
        return source.directory
    if isinstance(source, SmbSourceConfig):
        return source.path or None
    return None


# -- the one builder ----------------------------------------------------


def _coerce(hint, value, where, key, f, cls):
    """Coerce one JSON value against one type hint, or raise ConfigError."""
    if value is MISSING or value is None:
        if value is None and type(None) in get_args(hint):
            return None
        if (f.default is MISSING and f.default_factory is MISSING) or (cls, f.name) in _REQUIRED:
            raise ConfigError(f"{where}: missing required key {key!r}")
        return f.default if f.default is not MISSING else f.default_factory()
    origin = get_origin(hint)
    if origin is Union or origin is UnionType:
        members = [a for a in get_args(hint) if a is not type(None)]
        dcs = [a for a in members if is_dataclass(a)]
        if len(dcs) > 1:
            return _build_source(value, key)
        if len(dcs) == 1:
            return _build(dcs[0], value, key)
        return _coerce(members[0], value, where, key, f, cls)
    if hint is str:
        if not isinstance(value, str):
            raise ConfigError(f"{where}: {key!r} must be a string, got {value!r}")
        if value == "" and not (f.default == "" and (cls, f.name) not in _REQUIRED):
            raise ConfigError(f"{where}: {key!r} must be a non-empty string, got ''")
        return value
    if hint is bool:
        if not isinstance(value, bool):
            raise ConfigError(f"{where}: {key!r} must be true/false, got {value!r}")
        return value
    if hint is int or hint is float:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or (hint is int and not isinstance(value, int))
        ):
            want = "an integer" if hint is int else "a number"
            raise ConfigError(f"{where}: {key!r} must be {want}, got {value!r}")
        lo, hi = _RANGES.get(key, (None, None))
        if lo is not None and value < lo:
            raise ConfigError(f"{where}: {key!r} must be >= {lo}, got {value}")
        if hi is not None and value > hi:
            raise ConfigError(f"{where}: {key!r} must be <= {hi}, got {value}")
        return value
    if origin is tuple:
        if not isinstance(value, list):
            raise ConfigError(f"{where}: {key!r} must be a list, got {type(value).__name__}")
        elem = get_args(hint)[0]
        if is_dataclass(elem):
            return tuple(_build(elem, e, f"{where}.{key}[{i}]") for i, e in enumerate(value))
        return tuple(value)
    if origin is dict:
        if not isinstance(value, dict):
            raise ConfigError(f"{where}: {key!r} must be an object, got {type(value).__name__}")
        for k, v in value.items():
            if not isinstance(k, str) or not isinstance(v, str):
                raise ConfigError(f"{where}: {key!r} entry {k!r} must be a string, got {v!r}")
        return value
    if is_dataclass(hint):
        if not isinstance(value, dict):
            raise ConfigError(f"{key}: must be an object")
        return _build(hint, value, key)
    raise ConfigError(f"{where}: {key!r} has an unsupported type {hint!r}")


def _build(cls, data, where):
    """Build a dataclass from a dict, from its fields and hints.

    Unknown keys, missing required fields and wrong types all raise
    ConfigError naming the first problem. A section embedding a
    transport (destination, remote-dir, remote-files) carries the
    transport keys inline; they are split out here.
    """
    if not isinstance(data, dict):
        raise ConfigError(f"{where}: must be an object")
    hints = get_type_hints(cls)
    flds = {f.name: f for f in fields(cls)}
    kwargs = {}
    if "transport" in flds:
        tdata = {k: data[k] for k in _TRANSPORT_KEYS if k in data}
        tdata.setdefault("transport", "ftp" if cls is not DestinationConfig else "local")
        kwargs["transport"] = _build(TransportConfig, tdata, where)
    jkeys = {_FIELD_KEY.get(name, name) for name in flds}
    allowed = jkeys | (_TRANSPORT_KEYS if "transport" in flds else set())
    for key in data:
        if key not in allowed:
            raise ConfigError(f"{where}: unknown key {key!r} (known: {sorted(allowed)})")
    for name, f in flds.items():
        if name == "transport":
            continue
        jkey = _FIELD_KEY.get(name, name)
        kwargs[name] = _coerce(hints[name], data.get(jkey, MISSING), where, jkey, f, cls)
    if cls is TransportConfig:
        kind = kwargs["kind"]
        if kind not in ("local", "ftp", "sftp"):
            raise ConfigError(f"{where}: unknown transport {kind!r} (want local|ftp|sftp)")
        if kind == "local":
            for key in (
                "host",
                "port",
                "username",
                "password",
                "password_env",
                "key_file",
                "key_file_env",
            ):
                if key in data:
                    raise ConfigError(f"{where}: {key!r} does not apply to a 'local' transport")
            if "root" not in data:
                raise ConfigError(f"{where}: missing required key 'root'")
            if not kwargs["root"]:
                raise ConfigError(f"{where}: 'root' must be a non-empty string, got ''")
        else:
            if "root" in data:
                raise ConfigError(f"{where}: 'root' does not apply to an ftp/sftp transport")
            if "host" not in data:
                raise ConfigError(f"{where}: missing required key 'host'")
            if not kwargs["host"]:
                raise ConfigError(f"{where}: 'host' must be a non-empty string, got ''")
        if "port" not in data:
            kwargs["port"] = 22 if kind == "sftp" else 21
    elif cls is RemoteFilesSourceConfig:
        if not kwargs["files"]:
            raise ConfigError(f"{where}: 'files' must be a non-empty list")
    elif cls is WebsiteSourceConfig:
        url = kwargs["url"]
        if not url.startswith(("http://", "https://")):
            raise ConfigError(f"{where}: 'url' must start with http:// or https://, got {url!r}")
    elif cls is DeliverConfig:
        url = kwargs["upload_url"]
        if not url.startswith(("http://", "https://")):
            raise ConfigError(
                f"{where}: 'upload_url' must start with http:// or https://, got {url!r}"
            )
    elif cls is RemoteFile:
        name = kwargs["name"]
        if "/" in name or "\\" in name:
            raise ConfigError(f"{where}: 'name' must not contain a path separator, got {name!r}")
    elif cls is SeleniumSourceConfig or cls is PlaywrightSourceConfig:
        check = validate_actions if cls is SeleniumSourceConfig else validate_playwright_actions
        error = SeleniumSourceError if cls is SeleniumSourceConfig else PlaywrightSourceError
        try:
            check(list(kwargs["actions"]))
        except error as e:
            raise ConfigError(f"{where}: {e}") from e
    elif cls is AgentConfig:
        for key in ("job", "category"):
            v = kwargs[key]
            if v is not None and ("/" in v or "\\" in v):
                raise ConfigError(f"{where}: {key!r} must not contain a path separator, got {v!r}")
    return cls(**kwargs)


def _build_source(data, where):
    """Build one source section: dispatch on its "type" key."""
    if not isinstance(data, dict):
        raise ConfigError(f"{where}: must be an object")
    stype = data.get("type")
    for cls in get_args(SourceConfig):
        if cls.type == stype:
            rest = {k: v for k, v in data.items() if k != "type"}
            return _build(cls, rest, where)
    want = "|".join(c.type for c in get_args(SourceConfig))
    raise ConfigError(f"{where}: unknown source type {stype!r} (want {want})")


def parse_config(data: object) -> AgentConfig:
    """Parse a raw (JSON-decoded) config dict into a typed AgentConfig.

    Raises ConfigError naming the first problem, before any run starts.
    """
    if not isinstance(data, dict):
        raise ConfigError(f"config must be an object, got {type(data).__name__}")
    variables = data.get("variables")
    if variables is not None:
        if not isinstance(variables, dict):
            raise ConfigError(
                "config: 'variables' must be an object of name -> value, "
                f"got {type(variables).__name__}"
            )
        for name, value in variables.items():
            if not isinstance(name, str) or not name.isidentifier():
                raise ConfigError(f"config: variable names must be identifiers, got {name!r}")
            if name in TEMPLATE_BUILTINS:
                raise ConfigError(
                    f"config: variable {name!r} collides with built-in token {name!r}"
                )
            if not isinstance(value, str):
                raise ConfigError(f"config: variable {name!r} must be a string, got {value!r}")
    cfg = _build(AgentConfig, data, "config")
    try:
        get_procedure(cfg.procedure)
    except ValueError as e:
        raise ConfigError(f"config: {e}") from e
    try:
        render_name(
            cfg.rename_pattern,
            name="x",
            ext=".csv",
            job=cfg.job or "job",
            run_id="0" * 16,
            variables=cfg.variables,
            date_format=cfg.date_format,
            time_format=cfg.time_format,
        )
    except (ValueError, KeyError) as e:
        raise ConfigError(f"config: 'rename_pattern' {cfg.rename_pattern!r} is invalid: {e}") from e
    ctx = template_context(cfg.job, cfg.category, cfg.variables, cfg.date_format, cfg.time_format)
    templates = []
    if cfg.destination.directory:
        templates.append(("destination", cfg.destination.directory))
    if isinstance(cfg.source, RemoteFilesSourceConfig):
        for i, f in enumerate(cfg.source.files):
            for t in (f.directory, f.name):
                if t:
                    templates.append((f"source.files[{i}]", t))
    for where, template in templates:
        try:
            render_template(template, ctx)
        except ValueError as e:
            raise ConfigError(f"{where}: {e}") from e
    return cfg


def parse_source(data: object) -> SourceConfig:
    """Parse one ``source`` section on its own (tests, and anything
    that builds a source without a whole config). Templates are
    checked against dummy job/kind values: only unknown tokens
    other than {job}/{category} are reported here."""
    src = _build_source(data, "source")
    if isinstance(src, RemoteFilesSourceConfig):
        ctx = template_context("job", "category", {}, "%Y%m%d", "%H%M%S")
        for i, f in enumerate(src.files):
            for template in (f.directory, f.name):
                if template:
                    try:
                        render_template(template, ctx)
                    except ValueError as e:
                        raise ConfigError(f"source.files[{i}]: {e}") from e
    return src


def parse_transport(data: object, *, default_kind: str = "ftp") -> TransportConfig:
    """Parse one transport section on its own."""
    if not isinstance(data, dict):
        raise ConfigError("transport: must be an object")
    return _build(
        TransportConfig, {**data, "transport": data.get("transport", default_kind)}, "transport"
    )


def load_config(path: str | Path) -> AgentConfig:
    """Read a config file (JSON), import its optional procedures
    module, and parse it. A malformed file, a missing procedures
    module, or an invalid config raises with the reason."""
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"config file {path} is not valid JSON: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError(f"config must be an object, got {type(raw).__name__}")
    # Optional local procedures file: extra business procedures that
    # live next to the config, imported without touching this agent.
    # Imported BEFORE parsing, so a custom procedure name validates.
    extra = raw.get("procedures_module")
    if extra:
        __import__(extra)
    return parse_config(raw)
