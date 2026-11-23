"""Infrastructure -- config parser: JSON dict -> typed config.

The watcher config file is validated once, at startup, before
any scan starts. ``parse_config`` returns a frozen
``WatcherConfig``; anything wrong raises ``ConfigError`` listing
EVERY problem found (missing keys, wrong types, unknown keys,
bad transport kinds, out-of-range values) instead of failing on
the first one -- and instead of failing when a job's first
manifest arrives, after the run was already claimed, with a bare
KeyError. ``load_config`` reads a file and parses it;
``--check-config`` runs just this step and exits.

The parser checks shape only. It does not check that the FTP
hosts, the share root or the ledger path exist -- those are the
deployment machine's concern -- and it does not connect to
anything. A manifest for a job that is not in ``jobs`` at all is
NOT a config error: the watcher dead-letters it as an unknown job
by design, and the parser leaves that alone.

The shape checks here deliberately mirror the agent's parser
(``fileflow_agent.infrastructure.config``): the two projects
share no code by design -- small, stable, per-machine -- so the
small helper class is duplicated rather than imported.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(ValueError):
    """The config file is invalid; str() lists every problem found."""


# -- typed config -------------------------------------------------------


@dataclass(frozen=True)
class TransportConfig:
    """One transport, wherever it appears: FTP 1 (``intake``) or
    FTP 2 (``processing``). ``kind`` selects the fields that
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
class IntakeConfig:
    """FTP 1: where the agent's manifest batches land, and where
    finished originals are archived (per job)."""

    transport: TransportConfig = field(default_factory=TransportConfig)
    incoming_dir: str = "incoming"
    done_dir: str = "done"
    failed_dir: str = "failed"


@dataclass(frozen=True)
class ProcessingConfig:
    """FTP 2: where batches go for external processing, and where
    the processor reports the outcome (per job)."""

    transport: TransportConfig = field(default_factory=TransportConfig)
    processing_dir: str = "processing"
    success_dir: str = "success"
    failed_dir: str = "failed"


@dataclass(frozen=True)
class NotifyConfig:
    """Email on batch completion. The SMTP password comes from the
    env var named in ``password_env`` (never a literal)."""

    smtp_host: str = ""
    smtp_port: int = 587
    from_addr: str = ""
    to: list[str] = field(default_factory=list)
    username: str = ""
    password_env: str | None = None
    use_tls: bool = True


@dataclass(frozen=True)
class OutboundConfig:
    requests_dir: str = "upload-requests"
    archive_dir: str = "upload-archive"
    min_age_seconds: float = 2


@dataclass(frozen=True)
class WatcherConfig:
    intake: IntakeConfig
    processing: ProcessingConfig
    jobs: list[str] = field(default_factory=list)
    ledger_db: str = ""
    share_root: str | None = None
    log_file: str | None = None
    notify: NotifyConfig | None = None
    outbound: OutboundConfig | None = None


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

    def _rel_path(self, d: dict, key: str, where: str, default: str) -> str:
        """A relative path under a root: no absolute path, no ".."."""
        v = self._opt_str(d, key, where, default=default)
        if v and (Path(v).is_absolute() or ".." in Path(v).parts):
            self.fail(where, f"{key!r} must stay under its root, got {v!r}")
            return default
        return v


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
_TOP_KEYS = {
    "intake",
    "processing",
    "jobs",
    "share_root",
    "ledger_db",
    "log_file",
    "notify",
    "outbound",
}
_NOTIFY_KEYS = {"smtp_host", "smtp_port", "from", "to", "username", "password_env", "use_tls"}


def _parse_transport(p: _Parser, data: object, where: str) -> TransportConfig:
    if not isinstance(data, dict):
        p.fail(where, "must be an object")
        return TransportConfig()
    kind = p._opt_str(data, "transport", where, default="local")
    if kind not in ("local", "ftp", "sftp"):
        p.fail(where, f"'transport' must be local|ftp|sftp, got {kind!r}")
        kind = "local"
    host = p._opt_str(data, "host", where, default="")
    if kind in ("ftp", "sftp") and not host and "host" not in data:
        p.fail(where, f"'host' is required for transport {kind!r}")
    root = p._opt_str(data, "root", where, default="")
    if kind == "local" and not root and "root" not in data:
        p.fail(where, "'root' is required for transport 'local'")
    default_port = 22 if kind == "sftp" else 21
    return TransportConfig(
        kind=kind,
        host=host,
        port=p._int(data, "port", where, default_port, minimum=1, maximum=65535),
        username=p._opt_str(data, "username", where, default="") or "",
        password_env=p._opt_str(data, "password_env", where),
        password=p._opt_str(data, "password", where, allow_empty=True),
        key_file_env=p._opt_str(data, "key_file_env", where),
        key_file=p._opt_str(data, "key_file", where, allow_empty=True),
        root=root,
    )


def _parse_intake(p: _Parser, data: object, where: str) -> IntakeConfig:
    if not isinstance(data, dict):
        p.fail(where, "must be an object")
        return IntakeConfig()
    p._unknown(data, _TRANSPORT_KEYS | {"incoming_dir", "done_dir", "failed_dir"}, where)
    return IntakeConfig(
        transport=_parse_transport(p, data, where),
        incoming_dir=p._rel_path(data, "incoming_dir", where, default="incoming"),
        done_dir=p._rel_path(data, "done_dir", where, default="done"),
        failed_dir=p._rel_path(data, "failed_dir", where, default="failed"),
    )


def _parse_processing(p: _Parser, data: object, where: str) -> ProcessingConfig:
    if not isinstance(data, dict):
        p.fail(where, "must be an object")
        return ProcessingConfig()
    p._unknown(
        data,
        _TRANSPORT_KEYS | {"processing_dir", "success_dir", "failed_dir"},
        where,
    )
    return ProcessingConfig(
        transport=_parse_transport(p, data, where),
        processing_dir=p._rel_path(data, "processing_dir", where, default="processing"),
        success_dir=p._rel_path(data, "success_dir", where, default="success"),
        failed_dir=p._rel_path(data, "failed_dir", where, default="failed"),
    )


def _parse_notify(p: _Parser, data: object, where: str) -> NotifyConfig:
    if not isinstance(data, dict):
        p.fail(where, "must be an object")
        return NotifyConfig()
    p._unknown(data, _NOTIFY_KEYS, where)
    smtp_host = p._str(data, "smtp_host", where)
    from_addr = p._str(data, "from", where)
    to_raw = data.get("to")
    to: list[str] = []
    if to_raw is None:
        p.fail(where, "missing required key 'to'")
    elif isinstance(to_raw, str):
        if not to_raw:
            p.fail(where, "'to' must be a non-empty string")
        else:
            to = [to_raw]
    elif isinstance(to_raw, list):
        for addr in to_raw:
            if not isinstance(addr, str) or not addr:
                p.fail(where, f"'to' entries must be non-empty strings, got {addr!r}")
                continue
            to.append(addr)
        if not to:
            p.fail(where, "'to' must not be empty")
    else:
        p.fail(where, f"'to' must be a string or a list of strings, got {to_raw!r}")
    return NotifyConfig(
        smtp_host=smtp_host,
        smtp_port=p._int(data, "smtp_port", where, 587, minimum=1, maximum=65535),
        from_addr=from_addr,
        to=to,
        username=p._opt_str(data, "username", where, default="") or "",
        password_env=p._opt_str(data, "password_env", where),
        use_tls=p._bool(data, "use_tls", where, True),
    )


def _parse_outbound(p: _Parser, data: object, where: str) -> OutboundConfig:
    if not isinstance(data, dict):
        p.fail(where, "must be an object")
        return OutboundConfig()
    p._unknown(data, {"requests_dir", "archive_dir", "min_age_seconds"}, where)
    return OutboundConfig(
        requests_dir=p._rel_path(data, "requests_dir", where, default="upload-requests"),
        archive_dir=p._rel_path(data, "archive_dir", where, default="upload-archive"),
        min_age_seconds=p._num(data, "min_age_seconds", where, 2, minimum=0),
    )


def _parse_watcher(p: _Parser, data: dict) -> WatcherConfig:
    p._unknown(data, _TOP_KEYS, "config")
    ledger_db = p._str(data, "ledger_db", "config")
    intake = IntakeConfig()
    if "intake" not in data:
        p.fail("config", "missing required key 'intake'")
    else:
        intake = _parse_intake(p, data["intake"], "intake")
    processing = ProcessingConfig()
    if "processing" not in data:
        p.fail("config", "missing required key 'processing'")
    else:
        processing = _parse_processing(p, data["processing"], "processing")
    jobs: list[str] = []
    jobs_data = data.get("jobs")
    if not isinstance(jobs_data, list) or not jobs_data:
        p.fail("config", "'jobs' must be a non-empty list")
    else:
        for job in jobs_data:
            if not isinstance(job, str) or not job or "/" in job or "\\" in job:
                p.fail("config", f"job names must be plain names, got {job!r}")
                continue
            if job not in jobs:
                jobs.append(job)
    share_root = p._opt_str(data, "share_root", "config")
    log_file = p._opt_str(data, "log_file", "config")
    notify = None
    if data.get("notify") is not None:
        notify = _parse_notify(p, data["notify"], "notify")
    outbound = None
    if data.get("outbound") is not None:
        outbound = _parse_outbound(p, data["outbound"], "outbound")
    if outbound is not None and not share_root:
        p.fail("config", "'share_root' is required when 'outbound' is set")
    return WatcherConfig(
        intake=intake,
        processing=processing,
        jobs=jobs,
        ledger_db=ledger_db,
        share_root=share_root,
        log_file=log_file,
        notify=notify,
        outbound=outbound,
    )


def parse_config(data: object) -> WatcherConfig:
    """Parse a raw (JSON-decoded) config dict into a typed WatcherConfig.

    Raises ConfigError listing every problem, before any scan starts.
    """
    if not isinstance(data, dict):
        raise ConfigError(f"config must be an object, got {type(data).__name__}")
    p = _Parser()
    cfg = _parse_watcher(p, data)
    p.done()
    return cfg


def load_config(path: str | Path) -> WatcherConfig:
    """Read a config file (JSON) and parse it. A malformed file or an
    invalid config raises with the reason."""
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"config file {path} is not valid JSON: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError(f"config must be an object, got {type(raw).__name__}")
    return parse_config(raw)
