"""Application -- reconcile use case (daily check).

Lists the runs still in flight: SENT_TO_PROCESSING runs with
their per-file outcome status and how long each file has waited
since the handoff. A report, not a repair: no state changes, no
files moved. Returns a list of lines (empty = nothing in flight).
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..domain.ports import RunRepository


def _fmt_wait(seconds: float) -> str:
    """A wait duration in words: 90 -> '1m 30s', 90000 -> '1d 1h'."""
    seconds = max(0, int(seconds))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class ReconcileUseCase:
    def __init__(self, *, repository: RunRepository):
        self.repository = repository

    def run(self) -> list[str]:
        lines: list[str] = []
        now = datetime.now(timezone.utc)
        for run in self.repository.list_by_state("SENT_TO_PROCESSING"):
            files = run.detail.get("files", [])
            outcomes = run.detail.get("outcomes", {})
            pending = [f.get("name") for f in files if f.get("name") not in outcomes]
            done = sorted(f"{name}={entry.get('outcome', '?')}" for name, entry in outcomes.items())
            sent_at = _parse_time(run.updated_at)
            wait = f"waiting {_fmt_wait((now - sent_at).total_seconds())}" if sent_at else ""
            lines.append(
                f"{run.run_id} job={run.job}: {len(done)} done, {len(pending)} waiting"
                + (f" ({', '.join(done)})" if done else "")
                + (f" ({', '.join(pending)})" if pending else "")
                + (f" [{wait}]" if wait else "")
            )
        return lines
