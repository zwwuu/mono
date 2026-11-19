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


def render_name(pattern: str, *, name: str, ext: str, job: str, run_id: str) -> str:
    """Render a rename pattern. Tokens: {date} {time} {name} {ext} {job} {run_id}."""
    now = datetime.now()
    rendered = pattern.format(
        date=now.strftime("%Y%m%d"),
        time=now.strftime("%H%M%S"),
        name=name,
        ext=ext,
        job=job,
        run_id=run_id,
    )
    rendered = safe_name(rendered)
    if rendered in (".", "..", "") or "/" in rendered or "\\" in rendered:
        raise ValueError(f"rename pattern produced an unsafe name: {pattern!r}")
    return rendered
