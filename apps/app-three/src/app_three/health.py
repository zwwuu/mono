"""URL health checks for app-three (stdlib urllib only).

An HTTP response — even a 4xx/5xx — counts as UP: the server
answered. Connection failures raise, so the retry wrapper gets a
chance, and only a final failure becomes a DOWN result.
"""

import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from common.utils import now_iso, retry


def check_url(url, timeout=5):
    """Check one URL. Returns a result dict; raises on connection failure."""
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            code = resp.status
    except urllib.error.HTTPError as e:
        code = e.code
        e.close()
    elapsed_ms = int((time.monotonic() - started) * 1000)
    return {"url": url, "status": "UP", "status_code": code, "elapsed_ms": elapsed_ms, "error": ""}


@retry(max_attempts=2, delay=0.5)
def _check_with_retry(url, timeout):
    return check_url(url, timeout)


def _check_one(url, timeout):
    """One URL's result; a URL that never answers becomes DOWN, never a crash."""
    try:
        return _check_with_retry(url, timeout)
    except (OSError, ValueError) as e:
        return {"url": url, "status": "DOWN", "status_code": None, "elapsed_ms": 0, "error": str(e)}


def check_all(urls, timeout=5, max_workers=5):
    """Check every URL in parallel; results stay in input order."""
    urls = list(urls)
    if not urls:
        return []
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        return list(pool.map(_check_one, urls, [timeout] * len(urls)))


def build_summary(results):
    """Aggregate per-URL results into a report dict."""
    down = [r for r in results if r["status"] != "UP"]
    return {
        "generated_at": now_iso(),
        "total": len(results),
        "up": len(results) - len(down),
        "down": len(down),
        "results": results,
    }


def format_summary(summary):
    """Plain-text version of the report, for the email body."""
    lines = [
        f"Health check: {summary['up']}/{summary['total']} up, {summary['down']} down.",
        "",
    ]
    for result in summary["results"]:
        line = f"{result['status']} {result['url']} (HTTP {result['status_code']})"
        if result["error"]:
            line += f" — {result['error']}"
        lines.append(line)
    return "\n".join(lines)
