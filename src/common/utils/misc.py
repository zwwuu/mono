"""Small grab-bag: env vars, retry, timestamps."""

import functools
import os
import time
from datetime import datetime, timezone

__all__ = ["getenv", "getenv_bool", "getenv_int", "now_iso", "retry"]


def getenv(name, default=""):
    return os.environ.get(name, default)


def getenv_int(name, default=0):
    try:
        return int(os.environ.get(name, "")) if os.environ.get(name) else default
    except ValueError:
        return default


def getenv_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.lower() in ("1", "true", "yes", "on")


def retry(max_attempts=3, delay=1.0, backoff=2.0, exceptions=(Exception,)):
    """Retry a function with exponential backoff. Re-raises on the last try."""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            wait = delay
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions:
                    if attempt == max_attempts:
                        raise
                    time.sleep(wait)
                    wait *= backoff

        return wrapper

    return decorator


def now_iso():
    """Current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
