"""Infrastructure -- bounded retry with exponential backoff.

Only transient errors are retried (OSError: FTP/SFTP/network
blips). Validation errors (ValueError -- including a hash
mismatch -- ConfigError), manifest problems and unknown jobs fail
immediately: retrying them would fail the same way three times.
``sleep`` is injectable so tests pass a no-op.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from .filesystem import log

T = TypeVar("T")


def with_retries(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay_seconds: float = 1.0,
    sleep: Callable[[float], None] | None = None,
) -> T:
    """Call fn(), retrying OSError up to ``attempts`` times with
    exponential backoff (1s, then 2s, ...). Returns fn()'s value;
    after the last attempt the error raises exactly as before."""
    wait = sleep or time.sleep
    delay = base_delay_seconds
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except OSError as e:
            if attempt == attempts:
                raise
            log.warning(
                "attempt %d/%d failed: %s; retrying in %.1fs",
                attempt,
                attempts,
                e,
                delay,
            )
            wait(delay)
            delay *= 2
    raise AssertionError("unreachable")
