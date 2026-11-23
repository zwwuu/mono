"""Infrastructure -- config parser: JSON dict -> typed config.

The agent config file is validated once, at startup, before any
run starts. ``parse_config`` returns a frozen ``AgentConfig``;
anything wrong raises ``ConfigError`` listing EVERY problem found
(missing keys, wrong types, unknown keys, unknown source /
transport kinds, unknown template tokens, out-of-range values)
instead of failing on the first one -- and instead of failing
halfway through a run, after files were already downloaded, with
a bare KeyError. ``load_config`` reads a file and parses it;
``--check-config`` runs just this step and exits.

The parser checks shape only. It does not check that a named env
var is set -- the env may be set on the deployment machine, not
the machine running the check -- and it does not connect to
anything. Secrets still resolve at build time, env-first with a
literal fallback, as before.

One config is one job. The shapes here are the single source of
truth for the config file: ``source`` is a discriminated union on
``type`` (one dataclass per source), ``destination`` is a
transport plus an optional ``directory`` template, ``deliver``
is the website the pickup flow POSTs to.

The filename templates render against the job's own context: its
``variables`` (arbitrary name -> value tokens) plus {date}/{time}
in the job's own formats. The token set is the job's business
logic -- the parser validates templates against exactly that
context, so a typo still fails at startup but a future app's own
tokens are never 'unknown'.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from ..domain.integrity import render_name, render_template, template_context
from ..domain.procedures import get_procedure
from .playwright_source import PlaywrightSourceError
from .playwright_source import validate_actions as validate_playwright_actions
from .selenium_source import SeleniumSourceError, validate_actions

SOURCE_TYPES = (
    "website",
    "local",
    "local-dir",
    "remote-dir",
    "remote-files",
    "smb",
    "selenium",
    "playwright",
)
TRANSPORT_TYPES = ("local", "ftp", "sftp")
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
    """The config file is invalid; str() lists every problem found."""


# -- typed config -------------------------------------------------------


@dataclass(frozen=True)
class TransportConfig:
    """One transport, wherever it appears: the destination, or a
    remote source's connection. ``kind`` selects the fields that
    apply: ``local`` needs ``root``; ``ftp``/``sftp`` need ``host``
    (and take ``port``/``username``/``password``/``key_file``)."""

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
    """One email per completed run. ``from_addr`` is the config
    key ``"from"`` (a Python keyword) and the SMTP login username.
    The password comes from the env var named in ``password_env``,
    never from the config file."""

    smtp_host: str = ""
    smtp_port: int = 587
    from_addr: str = ""
    to: str = ""
    password_env: str | None = None


@dataclass(frozen=True)
class AgentConfig:
    """One config is one job. ``source`` is None for a pickup-only
    config (no download); ``deliver`` is None when the config has
    no pickup section; ``notify`` is None when the config sends no
    email (outcomes are only logged); ``log_file`` is None for
    console-only logging."""

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


# -- parsing ------------------------------------------------------------


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


class _Parser:
    """Collects every problem, then raises one ConfigError.

    Each reader returns a usable value (or a safe default) after
    recording a problem, so one bad field does not cascade into a
    dozen follow-on problems and parsing reports them all at once.
    """

    def __init__(self) -> None:
        self.problems: list[str] = []

    def fail(self, where: str, msg: str) -> None:
        self.problems.append(f"{where}: {msg}")

    def done(self) -> None:
        if self.problems:
            n = len(self.problems)
            raise ConfigError(
                f"invalid config: {n} problem{'s' if n != 1 else ''}:\n"
                + "\n".join(f"  - {p}" for p in self.problems)
            )

    def _unknown(self, d: dict, allowed: set[str], where: str) -> None:
        for key in d:
            if key not in allowed:
                self.fail(where, f"unknown key {key!r} (known: {sorted(allowed)})")

    def _str(self, d: dict, key: str, where: str) -> str:
        """Required non-empty string; "" on failure (already reported)."""
        if key not in d:
            self.fail(where, f"missing required key {key!r}")
            return ""
        return self._opt_str(d, key, where, default="")

    def _opt_str(
        self,
        d: dict,
        key: str,
        where: str,
        *,
        default: str | None = None,
        allow_empty: bool = False,
    ) -> str | None:
        if key not in d or d[key] is None:
            return default
        v = d[key]
        if not isinstance(v, str) or (not allow_empty and v == ""):
            want = "a string" if allow_empty else "a non-empty string"
            self.fail(where, f"{key!r} must be {want}, got {v!r}")
            return default
        return v

    def _int(
        self,
        d: dict,
        key: str,
        where: str,
        default: int,
        *,
        minimum: int | None = None,
        maximum: int | None = None,
    ) -> int:
        if key not in d:
            return default
        v = d[key]
        if not _is_int(v):
            self.fail(where, f"{key!r} must be an integer, got {v!r}")
            return default
        if minimum is not None and v < minimum:
            self.fail(where, f"{key!r} must be >= {minimum}, got {v}")
            return default
        if maximum is not None and v > maximum:
            self.fail(where, f"{key!r} must be <= {maximum}, got {v}")
            return default
        return v

    def _num(
        self,
        d: dict,
        key: str,
        where: str,
        default: float,
        *,
        minimum: float | None = None,
    ) -> float:
        if key not in d:
            return default
        v = d[key]
        if not _is_num(v):
            self.fail(where, f"{key!r} must be a number, got {v!r}")
            return default
        if minimum is not None and v < minimum:
            self.fail(where, f"{key!r} must be >= {minimum}, got {v}")
            return default
        return v

    def _bool(self, d: dict, key: str, where: str, default: bool) -> bool:
        if key not in d:
            return default
        v = d[key]
        if not isinstance(v, bool):
            self.fail(where, f"{key!r} must be true/false, got {v!r}")
            return default
        return v

    def _variables(self, d: dict, key: str, where: str, builtins: set[str]) -> dict:
        """The job's arbitrary template tokens: name -> string
        value. Names must be identifiers (a template field) and
        must not collide with a built-in token."""
        v = d.get(key)
        if v is None:
            return {}
        if not isinstance(v, dict):
            self.fail(where, f"{key!r} must be an object of name -> value, got {type(v).__name__}")
            return {}
        out: dict[str, str] = {}
        for name, value in v.items():
            if not isinstance(name, str) or not name.isidentifier():
                self.fail(where, f"variable names must be identifiers, got {name!r}")
                continue
            if name in builtins:
                self.fail(where, f"variable {name!r} collides with built-in token {name!r}")
                continue
            if not isinstance(value, str):
                self.fail(where, f"variable {name!r} must be a string, got {value!r}")
                continue
            out[name] = value
        return out

    def _template(
        self,
        template: str,
        where: str,
        job: str,
        category: str | None,
        variables: dict,
        date_format: str,
        time_format: str,
    ) -> None:
        """Render against the job's own context: catches unknown
        tokens, where 'unknown' means unknown to this job."""
        try:
            render_template(
                template, template_context(job, category, variables, date_format, time_format)
            )
        except ValueError as e:
            self.fail(where, str(e))


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


def _parse_transport(
    p: _Parser,
    data: dict,
    where: str,
    *,
    default_kind: str,
) -> TransportConfig:
    if not isinstance(data, dict):
        p.fail(where, "transport must be an object")
        return TransportConfig()
    kind = data.get("transport", default_kind)
    if kind not in TRANSPORT_TYPES:
        p.fail(where, f"unknown transport {kind!r} (want local|ftp|sftp)")
        return TransportConfig(kind="local")
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
                p.fail(where, f"{key!r} does not apply to a 'local' transport")
        return TransportConfig(kind="local", root=p._str(data, "root", where))
    if "root" in data:
        p.fail(where, "'root' does not apply to an ftp/sftp transport")
    return TransportConfig(
        kind=kind,
        host=p._str(data, "host", where),
        port=p._int(data, "port", where, 21 if kind == "ftp" else 22, minimum=1, maximum=65535),
        username=p._opt_str(data, "username", where, default="", allow_empty=True),
        password_env=p._opt_str(data, "password_env", where),
        password=p._opt_str(data, "password", where),
        key_file_env=p._opt_str(data, "key_file_env", where),
        key_file=p._opt_str(data, "key_file", where),
    )


def _parse_source(
    p: _Parser,
    data: object,
    where: str,
    job: str,
    category: str | None,
    variables: dict,
    date_format: str,
    time_format: str,
) -> SourceConfig:
    if not isinstance(data, dict):
        p.fail(where, "must be an object")
        return WebsiteSourceConfig(url="")
    source_type = data.get("type")
    if source_type not in SOURCE_TYPES:
        p.fail(
            where,
            f"unknown source type {source_type!r} (want {'|'.join(SOURCE_TYPES)})",
        )
        return WebsiteSourceConfig(url="")
    if source_type == "website":
        p._unknown(data, {"type", "url", "timeout_seconds"}, where)
        url = p._str(data, "url", where)
        if url and not url.startswith(("http://", "https://")):
            p.fail(where, f"'url' must start with http:// or https://, got {url!r}")
        return WebsiteSourceConfig(
            url=url,
            timeout_seconds=p._int(data, "timeout_seconds", where, 60, minimum=1),
        )
    if source_type == "local":
        p._unknown(data, {"type", "path"}, where)
        return LocalSourceConfig(path=p._str(data, "path", where))
    if source_type == "local-dir":
        p._unknown(data, {"type", "directory", "pattern", "min_age_seconds"}, where)
        return LocalDirSourceConfig(
            directory=p._str(data, "directory", where),
            pattern=p._opt_str(data, "pattern", where, default="*"),
            min_age_seconds=p._num(data, "min_age_seconds", where, 2, minimum=0),
        )
    if source_type == "remote-dir":
        p._unknown(
            data, _TRANSPORT_KEYS | {"type", "remote_dir", "pattern", "min_age_seconds"}, where
        )
        return RemoteDirSourceConfig(
            transport=_parse_transport(p, data, where, default_kind="ftp"),
            remote_dir=p._str(data, "remote_dir", where),
            pattern=p._opt_str(data, "pattern", where, default="*"),
            min_age_seconds=p._num(data, "min_age_seconds", where, 5, minimum=0),
        )
    if source_type == "remote-files":
        p._unknown(data, _TRANSPORT_KEYS | {"type", "files", "min_age_seconds"}, where)
        files = data.get("files")
        parsed: list[RemoteFile] = []
        if not isinstance(files, list) or not files:
            p.fail(where, "'files' must be a non-empty list")
        else:
            for i, entry in enumerate(files):
                entry_where = f"{where}.files[{i}]"
                if not isinstance(entry, dict):
                    p.fail(entry_where, "must be an object")
                    continue
                p._unknown(entry, {"directory", "name", "optional"}, entry_where)
                directory = p._str(entry, "directory", entry_where)
                name = p._str(entry, "name", entry_where)
                optional = p._bool(entry, "optional", entry_where, False)
                if "/" in name or "\\" in name:
                    p.fail(entry_where, f"'name' must not contain a path separator, got {name!r}")
                if directory:
                    p._template(
                        directory, entry_where, job, category, variables, date_format, time_format
                    )
                if name:
                    p._template(
                        name, entry_where, job, category, variables, date_format, time_format
                    )
                parsed.append(RemoteFile(directory=directory, name=name, optional=optional))
        return RemoteFilesSourceConfig(
            transport=_parse_transport(p, data, where, default_kind="ftp"),
            files=tuple(parsed),
            min_age_seconds=p._num(data, "min_age_seconds", where, 5, minimum=0),
        )
    if source_type == "smb":
        p._unknown(
            data,
            {
                "type",
                "server",
                "share",
                "path",
                "pattern",
                "min_age_seconds",
                "username",
                "password_env",
            },
            where,
        )
        return SmbSourceConfig(
            server=p._str(data, "server", where),
            share=p._str(data, "share", where),
            path=p._opt_str(data, "path", where, default="", allow_empty=True),
            pattern=p._opt_str(data, "pattern", where, default="*"),
            min_age_seconds=p._num(data, "min_age_seconds", where, 5, minimum=0),
            username=p._opt_str(data, "username", where, default="", allow_empty=True),
            password_env=p._opt_str(data, "password_env", where),
        )
    # selenium
    if source_type == "selenium":
        p._unknown(
            data,
            {"type", "actions", "headless", "timeout_seconds", "executable_path"},
            where,
        )
        actions = data.get("actions")
        try:
            validate_actions(actions)
        except SeleniumSourceError as e:
            p.fail(where, str(e))
            actions = []
        return SeleniumSourceConfig(
            actions=tuple(actions) if isinstance(actions, list) else (),
            headless=p._bool(data, "headless", where, True),
            timeout_seconds=p._int(data, "timeout_seconds", where, 60, minimum=1),
            executable_path=p._opt_str(data, "executable_path", where),
        )
    # playwright
    p._unknown(data, {"type", "actions", "headless", "timeout_seconds", "executable_path"}, where)
    actions = data.get("actions")
    try:
        validate_playwright_actions(actions)
    except PlaywrightSourceError as e:
        p.fail(where, str(e))
        actions = []
    return PlaywrightSourceConfig(
        actions=tuple(actions) if isinstance(actions, list) else (),
        headless=p._bool(data, "headless", where, True),
        timeout_seconds=p._int(data, "timeout_seconds", where, 60, minimum=1),
        executable_path=p._opt_str(data, "executable_path", where),
    )


def _parse_destination(
    p: _Parser,
    data: object,
    where: str,
    job: str,
    category: str | None,
    variables: dict,
    date_format: str,
    time_format: str,
) -> DestinationConfig:
    if not isinstance(data, dict):
        p.fail(where, "must be an object")
        return DestinationConfig(transport=TransportConfig())
    p._unknown(data, _TRANSPORT_KEYS | {"directory"}, where)
    transport = _parse_transport(p, data, where, default_kind="local")
    directory = p._opt_str(data, "directory", where)
    if directory:
        p._template(directory, where, job, category, variables, date_format, time_format)
    return DestinationConfig(transport=transport, directory=directory)


def _parse_deliver(p: _Parser, data: object, where: str) -> DeliverConfig:
    if not isinstance(data, dict):
        p.fail(where, "must be an object")
        return DeliverConfig(upload_url="")
    p._unknown(data, {"upload_url", "form_field", "auth_env", "timeout_seconds"}, where)
    url = p._str(data, "upload_url", where)
    if url and not url.startswith(("http://", "https://")):
        p.fail(where, f"'upload_url' must start with http:// or https://, got {url!r}")
    return DeliverConfig(
        upload_url=url,
        form_field=p._opt_str(data, "form_field", where, default="file"),
        auth_env=p._opt_str(data, "auth_env", where),
        timeout_seconds=p._int(data, "timeout_seconds", where, 60, minimum=1),
    )


def _parse_notify(p: _Parser, data: object, where: str) -> NotifyConfig:
    if not isinstance(data, dict):
        p.fail(where, "must be an object")
        return NotifyConfig()
    p._unknown(data, {"smtp_host", "smtp_port", "from", "to", "password_env"}, where)
    return NotifyConfig(
        smtp_host=p._str(data, "smtp_host", where),
        smtp_port=p._int(data, "smtp_port", where, 587, minimum=1, maximum=65535),
        from_addr=p._str(data, "from", where),
        to=p._str(data, "to", where),
        password_env=p._opt_str(data, "password_env", where),
    )


_TOP_KEYS = {
    "job",
    "category",
    "variables",
    "date_format",
    "time_format",
    "procedure",
    "expect_csv",
    "rename_pattern",
    "staging_dir",
    "ledger_db",
    "source",
    "destination",
    "deliver",
    "procedures_module",
    "notify",
    "log_file",
}


def _parse_agent(p: _Parser, data: dict) -> AgentConfig:
    p._unknown(data, _TOP_KEYS, "config")
    job = p._str(data, "job", "config")
    if "/" in job or "\\" in job:
        p.fail("config", f"'job' must not contain a path separator, got {job!r}")
    category = p._opt_str(data, "category", "config")
    if category is not None and ("/" in category or "\\" in category):
        p.fail("config", f"'category' must not contain a path separator, got {category!r}")
    variables = p._variables(data, "variables", "config", TEMPLATE_BUILTINS)
    date_format = p._opt_str(data, "date_format", "config", default="%Y%m%d")
    time_format = p._opt_str(data, "time_format", "config", default="%H%M%S")
    procedure = p._opt_str(data, "procedure", "config", default="rename-only")
    try:
        get_procedure(procedure)
    except ValueError as e:
        p.fail("config", str(e))
    expect_csv = p._bool(data, "expect_csv", "config", False)
    pattern = p._opt_str(data, "rename_pattern", "config", default="{date}_{name}{ext}")
    try:
        render_name(
            pattern,
            name="x",
            ext=".csv",
            job=job or "job",
            run_id="0" * 16,
            variables=variables,
            date_format=date_format,
            time_format=time_format,
        )
    except (ValueError, KeyError) as e:
        p.fail("config", f"'rename_pattern' {pattern!r} is invalid: {e}")
    staging_dir = p._str(data, "staging_dir", "config")
    ledger_db = p._str(data, "ledger_db", "config")
    source = (
        _parse_source(
            p, data["source"], "source", job, category, variables, date_format, time_format
        )
        if "source" in data
        else None
    )
    destination = _parse_destination(
        p,
        data.get("destination"),
        "destination",
        job,
        category,
        variables,
        date_format,
        time_format,
    )
    deliver = None
    if "deliver" in data:
        deliver = _parse_deliver(p, data["deliver"], "deliver")
    procedures_module = p._opt_str(data, "procedures_module", "config")
    notify = None
    if "notify" in data:
        notify = _parse_notify(p, data["notify"], "notify")
    log_file = p._opt_str(data, "log_file", "config")
    return AgentConfig(
        job=job,
        staging_dir=staging_dir,
        ledger_db=ledger_db,
        source=source,
        destination=destination,
        category=category,
        variables=variables,
        date_format=date_format,
        time_format=time_format,
        procedure=procedure,
        expect_csv=expect_csv,
        rename_pattern=pattern,
        deliver=deliver,
        procedures_module=procedures_module,
        notify=notify,
        log_file=log_file,
    )


def parse_config(data: object) -> AgentConfig:
    """Parse a raw (JSON-decoded) config dict into a typed AgentConfig.

    Raises ConfigError listing every problem, before any run starts.
    """
    if not isinstance(data, dict):
        raise ConfigError(f"config must be an object, got {type(data).__name__}")
    p = _Parser()
    cfg = _parse_agent(p, data)
    p.done()
    return cfg


def parse_source(data: object) -> SourceConfig:
    """Parse one ``source`` section on its own (tests, and anything
    that builds a source without a whole config). Templates are
    checked against dummy job/kind values: only unknown tokens
    other than {job}/{category} are reported here."""
    p = _Parser()
    src = _parse_source(
        p,
        data,
        "source",
        job="job",
        category="category",
        variables={},
        date_format="%Y%m%d",
        time_format="%H%M%S",
    )
    p.done()
    return src


def parse_transport(data: object, *, default_kind: str = "ftp") -> TransportConfig:
    """Parse one transport section on its own."""
    p = _Parser()
    t = _parse_transport(p, data, "transport", default_kind=default_kind)
    p.done()
    return t


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
