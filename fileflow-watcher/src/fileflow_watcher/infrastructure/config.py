"""Infrastructure -- config parser: JSON dict -> typed config.

The watcher config file is validated once, at startup, before
any scan starts. ``parse_config`` returns a frozen
``WatcherConfig``; anything wrong raises ``ConfigError`` listing
EVERY problem found (missing keys, wrong types, unknown keys,
unknown procedures, bad output patterns, out-of-range values)
instead of failing on the first one -- and instead of failing
when a job's first manifest arrives, after the run was already
claimed, with a bare KeyError. ``load_config`` reads a file,
imports the optional ``procedures_module`` (so custom procedure
names validate too), and parses it; ``--check-config`` runs just
this step and exits.

The parser checks shape only. It does not check that the FTP
root, the share root or the ledger path exist -- those are the
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
from datetime import datetime
from pathlib import Path

from ..domain.procedures import get_procedure


class ConfigError(ValueError):
    """The config file is invalid; str() lists every problem found."""


# -- typed config -------------------------------------------------------


@dataclass(frozen=True)
class JobConfig:
    """One job's routing: which procedure, where its outputs go,
    and whether it also stages outbound batches. ``share_dir`` is
    resolved at parse time (default: the job name).

    The output filename render is the job's business logic:
    ``variables`` are its arbitrary tokens and {date}/{time} use
    its own formats -- a future job with different formats or its
    own variables gets its own context, and the parser validates
    ``output_pattern`` against exactly that context."""

    procedure: str
    expect_csv: bool = False
    output_pattern: str = "{name}"
    share_dir: str = ""
    outbound: bool = False
    variables: dict[str, str] = field(default_factory=dict)
    date_format: str = "%Y%m%d"
    time_format: str = "%H%M%S"


@dataclass(frozen=True)
class OutboundConfig:
    requests_dir: str = "upload-requests"
    archive_dir: str = "upload-archive"
    min_age_seconds: float = 2


@dataclass(frozen=True)
class WatcherConfig:
    ftp_root: str
    share_root: str
    ledger_db: str
    jobs: dict[str, JobConfig] = field(default_factory=dict)
    outbound: OutboundConfig | None = None
    procedures_module: str | None = None


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

    def _output_pattern(
        self,
        pattern: str,
        where: str,
        job: str,
        variables: dict,
        date_format: str,
        time_format: str,
    ) -> None:
        """Render against the job's own context: catches unknown
        tokens, where 'unknown' means unknown to this job, and a
        pattern that would publish outside one filename."""
        now = datetime.now()
        context = dict(variables)
        context.update(
            name="x",
            run_id="0" * 16,
            job=job,
            date=now.strftime(date_format),
            time=now.strftime(time_format),
        )
        try:
            rendered = pattern.format_map(context)
        except KeyError as e:
            self.fail(
                where,
                f"'output_pattern' {pattern!r} uses unknown token {e}; "
                f"known tokens: {sorted(context)}",
            )
            return
        if "/" in rendered or "\\" in rendered:
            self.fail(
                where,
                f"'output_pattern' {pattern!r} must produce one filename, got {rendered!r}",
            )


_JOB_KEYS = {
    "procedure",
    "expect_csv",
    "output_pattern",
    "share_dir",
    "outbound",
    "variables",
    "date_format",
    "time_format",
}
# Every token the framework itself provides in an output pattern.
_JOB_BUILTINS = {"name", "run_id", "job", "date", "time"}
_TOP_KEYS = {"ftp_root", "share_root", "ledger_db", "jobs", "outbound", "procedures_module"}


def _parse_job(p: _Parser, data: object, where: str, job: str) -> JobConfig:
    if not isinstance(data, dict):
        p.fail(where, "must be an object")
        return JobConfig(procedure="")
    p._unknown(data, _JOB_KEYS, where)
    procedure = p._str(data, "procedure", where)
    if procedure:
        try:
            get_procedure(procedure)
        except ValueError as e:
            p.fail(where, str(e))
    pattern = p._opt_str(data, "output_pattern", where, default="{name}")
    variables = p._variables(data, "variables", where, _JOB_BUILTINS)
    date_format = p._opt_str(data, "date_format", where, default="%Y%m%d")
    time_format = p._opt_str(data, "time_format", where, default="%H%M%S")
    p._output_pattern(pattern, where, job, variables, date_format, time_format)
    share_dir = p._rel_path(data, "share_dir", where, default=job)
    return JobConfig(
        procedure=procedure,
        expect_csv=p._bool(data, "expect_csv", where, False),
        output_pattern=pattern,
        share_dir=share_dir,
        outbound=p._bool(data, "outbound", where, False),
        variables=variables,
        date_format=date_format,
        time_format=time_format,
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
    ftp_root = p._str(data, "ftp_root", "config")
    share_root = p._str(data, "share_root", "config")
    ledger_db = p._str(data, "ledger_db", "config")
    jobs: dict[str, JobConfig] = {}
    jobs_data = data.get("jobs")
    if not isinstance(jobs_data, dict) or not jobs_data:
        p.fail("config", "'jobs' must be a non-empty object")
    else:
        for job, job_data in jobs_data.items():
            if not isinstance(job, str) or not job or "/" in job or "\\" in job:
                p.fail("config", f"job names must be plain names, got {job!r}")
                continue
            jobs[job] = _parse_job(p, job_data, f"jobs.{job}", job)
    outbound = None
    if data.get("outbound") is not None:
        outbound = _parse_outbound(p, data["outbound"], "outbound")
    procedures_module = p._opt_str(data, "procedures_module", "config")
    return WatcherConfig(
        ftp_root=ftp_root,
        share_root=share_root,
        ledger_db=ledger_db,
        jobs=jobs,
        outbound=outbound,
        procedures_module=procedures_module,
    )


def parse_config(data: object) -> WatcherConfig:
    """Parse a raw (JSON-decoded) config dict into a typed WatcherConfig.

    Raises ConfigError listing every problem, before any scan starts.
    The procedure registry must already hold the custom procedures
    (``load_config`` imports ``procedures_module`` first).
    """
    if not isinstance(data, dict):
        raise ConfigError(f"config must be an object, got {type(data).__name__}")
    p = _Parser()
    cfg = _parse_watcher(p, data)
    p.done()
    return cfg


def load_config(path: str | Path) -> WatcherConfig:
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
    # live next to the config, imported without touching this watcher.
    # Imported BEFORE parsing, so a custom procedure name validates.
    extra = raw.get("procedures_module")
    if extra:
        __import__(extra)
    return parse_config(raw)
