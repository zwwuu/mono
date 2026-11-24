"""Pure domain rules: no I/O, no dependencies outside the stdlib.

Safe rename patterns: the checks the pipeline's naming rests on.
The manifest rules (verification, idempotency key) live in the
shared ``fileflow_contract`` package, not here.
"""

from __future__ import annotations

import os
import posixpath
import re
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
    run_id: str,
    variables: dict | None = None,
) -> str:
    """Render a rename pattern. Tokens: the job's own ``variables``
    and the per-file {name} {ext} {run_id}. There are no built-in
    tokens -- no {date}, no {job}: a job that wants a date or a
    client name in its output names puts it in ``variables`` (or in
    the procedure, which owns the name by business logic)."""
    return Context(variables).render_name(pattern, name=name, ext=ext, run_id=run_id)


class Context:
    """One run's template values: renders {placeholders} and
    resolves paths. The values are exactly the job's declared
    ``variables`` -- nothing is injected. Per-render extras
    (name/ext/run_id) ride along on the call.

    Built fresh per run (per loop tick), never once at import or
    config load, so a template that reads a variable file (or a
    future variables source) sees this run's values.
    """

    def __init__(self, variables: dict | None = None):
        self.values = dict(variables or {})

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


def render_template(template: str, context: dict) -> str:
    """Render a directory/name template against a context. An unknown
    token fails loud instead of silently leaving "{token}" in a path."""
    try:
        return template.format_map(context)
    except KeyError as e:
        raise ValueError(
            f"template {template!r} uses unknown token {e}; known tokens: {sorted(context)}"
        ) from e
