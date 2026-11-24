"""Pure domain rules: no I/O, no dependencies outside the stdlib.

Safe rename patterns: the checks the pipeline's naming rests on.
The manifest rules (verification, idempotency key) live in the
shared ``fileflow_contract`` package, not here.
"""

from __future__ import annotations

import os
import posixpath
import re
from datetime import datetime
from pathlib import Path

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(name: str) -> str:
    """Strip path separators / unsafe chars so a name can never escape its dir."""
    name = Path(name).name
    return _SAFE.sub("_", name) or "file"


def render_name(
    pattern: str,
    *,
    name: str,
    ext: str,
    job: str,
    run_id: str,
    variables: dict | None = None,
    date_format: str = "%Y%m%d",
    time_format: str = "%H%M%S",
) -> str:
    """Render a rename pattern. Tokens: the job's context
    ({year} {month} {day} {date} {time} {job} {category} plus the
    job's own ``variables``) and the per-file {name} {ext} {run_id}.

    The filename render is business logic, not framework logic:
    {date}/{time} use the job's own formats (default YYYYMMDD /
    HHMMSS), and ``variables`` are the job's arbitrary tokens
    (e.g. a client or region name). A variable colliding with a
    built-in token is rejected by the config parser, so here the
    built-ins always win."""
    return Context(job, None, variables, date_format, time_format).render_name(
        pattern, name=name, ext=ext, run_id=run_id
    )


class Context:
    """One run's template values: renders {placeholders} and
    resolves paths. The values are exactly template_context's
    (year/month/day/date/time/job/category + the job's variables);
    per-render extras (name/ext/run_id) ride along on the call.

    Built fresh per run (per loop tick), never once at import or
    config load, so the date is the run's date -- a --loop process
    crossing midnight renders the new day, not the startup day.
    """

    def __init__(
        self,
        job: str,
        category: str | None = None,
        variables: dict | None = None,
        date_format: str = "%Y%m%d",
        time_format: str = "%H%M%S",
        *,
        when: datetime | None = None,
    ):
        now = when or datetime.now()
        self.values = dict(variables or {})
        self.values.update(
            year=f"{now.year:04d}",
            month=f"{now.month:02d}",
            day=f"{now.day:02d}",
            date=now.strftime(date_format),
            time=now.strftime(time_format),
            job=job,
        )
        if category is not None:
            self.values["category"] = str(category)

    def render(self, template: str, **extra: str) -> str:
        """Render a template against this run's values plus extras.
        An unknown token fails loud instead of silently leaving
        "{token}" in a path."""
        return render_template(template, {**self.values, **extra})

    def render_name(self, pattern: str, *, name: str, ext: str, run_id: str) -> str:
        """Render a rename pattern against this run's values plus
        the file's {name} {ext} {run_id}. The result is sanitized
        (safe_name) and must be a plain file name, never a path."""
        rendered = self.render(pattern, name=name, ext=ext, run_id=run_id)
        rendered = safe_name(rendered)
        if rendered in (".", "..", "") or "/" in rendered or "\\" in rendered:
            raise ValueError(f"rename pattern produced an unsafe name: {pattern!r}")
        return rendered

    def local_path(self, template: str, **extra: str) -> str:
        """Fill placeholders, then an absolute path on this machine.
        A relative path anchors to the current working directory --
        the same place a plain relative path would resolve -- not to
        the program's own directory."""
        rendered = self.render(template, **extra)
        return os.path.normpath(os.path.join(os.getcwd(), rendered))

    def remote_path(self, template: str, **extra: str) -> str:
        """Fill placeholders, then a normalized posix path. Remote
        paths are relative to the transport root and are never
        anchored to any local directory."""
        return posixpath.normpath(self.render(template, **extra))


def template_context(
    job: str,
    category: str | None = None,
    variables: dict | None = None,
    date_format: str = "%Y%m%d",
    time_format: str = "%H%M%S",
) -> dict:
    """Tokens for directory/name templates: the run date plus the
    job's own values. {year}/{month}/{day} are the date's
    zero-padded components; {date}/{time} use the job's own
    formats; {category} is there only when the job sets a category; and
    ``variables`` are the job's arbitrary tokens. The token set is
    the job's business logic -- a future app with different
    formats or its own variables gets its own context, and the
    parser validates templates against exactly this context."""
    return Context(job, category, variables, date_format, time_format).values


def render_template(template: str, context: dict) -> str:
    """Render a directory/name template against a context. An unknown
    token fails loud instead of silently leaving "{token}" in a path."""
    try:
        return template.format_map(context)
    except KeyError as e:
        raise ValueError(
            f"template {template!r} uses unknown token {e}; known tokens: {sorted(context)}"
        ) from e
