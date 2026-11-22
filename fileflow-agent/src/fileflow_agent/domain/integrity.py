"""Pure domain rules: no I/O, no dependencies outside the stdlib.

Safe rename patterns: the checks the pipeline's naming rests on.
The manifest rules (verification, idempotency key) live in the
shared ``fileflow_contract`` package, not here.
"""

from __future__ import annotations

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
    """Render a rename pattern. Built-in tokens: {date} {time}
    {name} {ext} {job} {run_id} -- plus the job's own ``variables``.

    The filename render is business logic, not framework logic:
    {date}/{time} use the job's own formats (default YYYYMMDD /
    HHMMSS), and ``variables`` are the job's arbitrary tokens
    (e.g. a client or region name). A variable colliding with a
    built-in token is rejected by the config parser, so here the
    built-ins always win."""
    now = datetime.now()
    context = dict(variables or {})
    context.update(
        date=now.strftime(date_format),
        time=now.strftime(time_format),
        name=name,
        ext=ext,
        job=job,
        run_id=run_id,
    )
    rendered = pattern.format_map(context)
    rendered = safe_name(rendered)
    if rendered in (".", "..", "") or "/" in rendered or "\\" in rendered:
        raise ValueError(f"rename pattern produced an unsafe name: {pattern!r}")
    return rendered


def template_context(
    job: str,
    kind: str | None = None,
    variables: dict | None = None,
    date_format: str = "%Y%m%d",
    time_format: str = "%H%M%S",
) -> dict:
    """Tokens for directory/name templates: the run date plus the
    job's own values. {year}/{month}/{day} are the date's
    zero-padded components; {date}/{time} use the job's own
    formats; {kind} is there only when the job sets a kind; and
    ``variables`` are the job's arbitrary tokens. The token set is
    the job's business logic -- a future app with different
    formats or its own variables gets its own context, and the
    parser validates templates against exactly this context."""
    now = datetime.now()
    context = dict(variables or {})
    context.update(
        year=f"{now.year:04d}",
        month=f"{now.month:02d}",
        day=f"{now.day:02d}",
        date=now.strftime(date_format),
        time=now.strftime(time_format),
        job=job,
    )
    if kind is not None:
        context["kind"] = str(kind)
    return context


def render_template(template: str, context: dict) -> str:
    """Render a directory/name template against a context. An unknown
    token fails loud instead of silently leaving "{token}" in a path."""
    try:
        return template.format_map(context)
    except KeyError as e:
        raise ValueError(
            f"template {template!r} uses unknown token {e}; known tokens: {sorted(context)}"
        ) from e
