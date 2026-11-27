"""Infrastructure -- Playwright source: fetch files by driving a real web page.

Some portals have no direct download URL: the file only appears after
logging in and clicking through links and buttons. A "playwright"
source declares those steps as actions; the runner plays them back
with Playwright (Chromium) and saves the resulting downloads. This
is the same action language as the "selenium" source and the
monorepo FileFlow browser source -- the actions and selectors below
are the same shapes, so a portal script written for one reads the
same in the other.

Playwright is an optional dependency (``pip install playwright``)
and Chromium comes bundled with it: run ``playwright install
chromium`` once on the PC. No Chrome install and no chromedriver --
the browser version is pinned to the Playwright package, so a
browser auto-update can never break the driver the way it can
with Selenium. Downloads use ``page.expect_download()``: the file
is handed to the runner directly instead of being found by
polling a download directory.

Action reference (each action is an object with an "action" field):
  {"action": "goto", "url": "https://portal.example.com/login"}
  {"action": "fill", "selector": "#username", "value": "alice"}
  {"action": "fill", "selector": "#password", "secret_env": "PORTAL_PASSWORD"}
      (secret_env = name of an environment variable; credentials never
      belong in the config file -- same rule as the other secrets)
  {"action": "click", "selector": "button[type=submit]"}
  {"action": "wait", "seconds": 2}
  {"action": "wait_for", "selector": "a.download", "timeout_seconds": 30}
  {"action": "download", "selector": "a.download", "timeout_seconds": 60}
      (clicks the selector and saves the browser download; every
      download action produces one file in the batch)

The source object may also carry "headless": false to watch the run,
"executable_path": "/path/to/chromium" for a non-bundled browser,
and "timeout_seconds" (default 60) for page loads and actions.

Selectors work exactly as in the selenium source: a plain CSS
string, or an object with a "kind": css, text, role, label,
placeholder, testid, xpath, row, chain. Selector kinds live in
SELECTOR_RESOLVERS, a plain dict of kind -> resolver(scope, spec) ->
Locator. To add a kind, write a resolver and register it -- no
changes to the action runner needed:

  from fileflow_agent.infrastructure.playwright_source import register_selector
  def by_data_action(scope, spec):
      return scope.locator(f"[data-action='{spec['data_action']}']")
  register_selector("data_action", by_data_action)

A resolver receives the page or a locator as scope, so it works
inside "chain" and "row" targets too.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from .filesystem import PART_SUFFIX, log

if TYPE_CHECKING:
    from .config import PlaywrightSourceConfig

ACTION_TYPES = ("goto", "fill", "click", "wait", "wait_for", "download")
_LEAF_KEYS = ("css", "text", "role", "label", "placeholder", "testid", "xpath")


class PlaywrightSourceError(Exception):
    pass


# ------------------------------------------------------------ selector core
def _norm(text):
    return " ".join((text or "").split())


def _match_text(text, match):
    """match is a string (exact) or {"equals"|"contains"|"regex": ...}."""
    if isinstance(match, str):
        match = {"equals": match}
    if not isinstance(match, dict):
        raise PlaywrightSourceError(f"row match must be a string or an object, got {match!r}")
    case_sensitive = match.get("case_sensitive", False)
    hay = text if case_sensitive else text.lower()
    if "equals" in match:
        want = match["equals"]
        want = want if case_sensitive else want.lower()
        return hay == want
    if "contains" in match:
        want = match["contains"]
        want = want if case_sensitive else want.lower()
        return want in hay
    if "regex" in match:
        flags = 0 if case_sensitive else re.IGNORECASE
        return re.search(match["regex"], text, flags) is not None
    raise PlaywrightSourceError(f"row match needs one of equals/contains/regex, got {match!r}")


def _resolve_css(scope, spec):
    return scope.locator(spec["css"])


def _resolve_text(scope, spec):
    return scope.get_by_text(spec["text"], exact=spec.get("exact", False))


def _resolve_role(scope, spec):
    kwargs = {"exact": spec.get("exact", False)}
    if "name" in spec:
        kwargs["name"] = spec["name"]
    return scope.get_by_role(spec["role"], **kwargs)


def _resolve_label(scope, spec):
    return scope.get_by_label(spec["label"], exact=spec.get("exact", False))


def _resolve_placeholder(scope, spec):
    return scope.get_by_placeholder(spec["placeholder"], exact=spec.get("exact", False))


def _resolve_testid(scope, spec):
    return scope.get_by_test_id(spec["testid"])


def _resolve_xpath(scope, spec):
    return scope.locator(f"xpath={spec['xpath']}")


def _resolve_row(scope, spec):
    table_spec = spec.get("table")
    table = resolve_selector(scope, table_spec) if table_spec else scope.locator("table").first
    rows = table.locator("tbody tr")
    if rows.count() == 0:
        rows = table.locator("tr")
    column = spec["column"]
    if isinstance(column, int):
        idx = column
    else:
        headers = table.locator("thead th").all_inner_texts()
        if not headers:
            first = table.locator("tr").first
            headers = first.locator("th").all_inner_texts()
        wanted = _norm(str(column)).lower()
        idx = None
        for i, header in enumerate(headers):
            if _norm(header).lower() == wanted:
                idx = i
                break
        if idx is None:
            raise PlaywrightSourceError(
                f"row selector: no column header {column!r}; headers are {headers}"
            )
    matched = []
    seen_values = []
    for i in range(rows.count()):
        row = rows.nth(i)
        cells = row.locator("td")
        if idx >= cells.count():
            continue
        text = _norm(cells.nth(idx).inner_text())
        seen_values.append(text)
        if _match_text(text, spec["match"]):
            matched.append(row)
    if not matched:
        raise PlaywrightSourceError(
            f"row selector: no row matched {spec['match']!r} in column {column!r} "
            f"({len(seen_values)} rows checked; values seen: {seen_values[:5]})"
        )
    nth = spec.get("nth", 0)
    if nth >= len(matched):
        raise PlaywrightSourceError(
            f"row selector: {len(matched)} row(s) matched but nth={nth} is out of range"
        )
    row = matched[nth]
    target = spec.get("target")
    return resolve_selector(row, target) if target else row


def _resolve_chain(scope, spec):
    current = scope
    for step in spec["steps"]:
        current = resolve_selector(current, step)
    return current


SELECTOR_RESOLVERS = {
    "css": _resolve_css,
    "text": _resolve_text,
    "role": _resolve_role,
    "label": _resolve_label,
    "placeholder": _resolve_placeholder,
    "testid": _resolve_testid,
    "xpath": _resolve_xpath,
    "row": _resolve_row,
    "chain": _resolve_chain,
}


def register_selector(kind, resolver):
    """Add (or replace) a selector kind. resolver(scope, spec) -> Locator."""
    SELECTOR_RESOLVERS[kind] = resolver


def _infer_kind(spec):
    present = [k for k in _LEAF_KEYS if k in spec]
    if len(present) == 1:
        return present[0]
    raise PlaywrightSourceError(
        f"selector object needs a 'kind' (one of {sorted(SELECTOR_RESOLVERS)}), got {spec!r}"
    )


def resolve_selector(scope, spec):
    """Resolve a selector (CSS string or selector object) to a Locator."""
    if isinstance(spec, str):
        if not spec:
            raise PlaywrightSourceError("selector string must not be empty")
        return scope.locator(spec)
    if not isinstance(spec, dict):
        raise PlaywrightSourceError(f"selector must be a string or an object, got {spec!r}")
    kind = spec.get("kind") or _infer_kind(spec)
    resolver = SELECTOR_RESOLVERS.get(kind)
    if resolver is None:
        raise PlaywrightSourceError(
            f"unknown selector kind {kind!r}; known kinds: {sorted(SELECTOR_RESOLVERS)}"
        )
    locator = resolver(scope, spec)
    # "row" applies nth to its matched rows internally; every other kind
    # applies it to the resolved locator.
    if kind != "row" and "nth" in spec:
        locator = locator.nth(spec["nth"])
    return locator


def validate_selector(spec, where="selector"):
    """Validate a selector; raises PlaywrightSourceError on anything invalid."""
    if isinstance(spec, str):
        if not spec:
            raise PlaywrightSourceError(f"{where}: selector string must not be empty")
        return spec
    if not isinstance(spec, dict):
        raise PlaywrightSourceError(f"{where}: selector must be a string or an object")
    kind = spec.get("kind") or _infer_kind(spec)
    if kind not in SELECTOR_RESOLVERS:
        raise PlaywrightSourceError(
            f"{where}: unknown selector kind {kind!r}; known kinds: {sorted(SELECTOR_RESOLVERS)}"
        )
    for key, label in (
        ("css", "css"),
        ("text", "text"),
        ("role", "role"),
        ("label", "label"),
        ("placeholder", "placeholder"),
        ("testid", "testid"),
        ("xpath", "xpath"),
    ):
        if kind == key and not isinstance(spec.get(label), str):
            raise PlaywrightSourceError(f"{where}: {key} selector needs a '{label}' string")
    if kind == "row":
        if not isinstance(spec.get("column"), (int, str)):
            raise PlaywrightSourceError(f"{where}: row selector needs a 'column' (name or index)")
        match = spec.get("match")
        if not isinstance(match, (str, dict)):
            raise PlaywrightSourceError(f"{where}: row selector needs a 'match' (string or object)")
        if isinstance(match, dict) and not any(k in match for k in ("equals", "contains", "regex")):
            raise PlaywrightSourceError(f"{where}: row match needs one of equals/contains/regex")
        if "table" in spec:
            validate_selector(spec["table"], where + ".table")
        if "target" in spec:
            validate_selector(spec["target"], where + ".target")
    if kind == "chain":
        steps = spec.get("steps")
        if not isinstance(steps, list) or not steps:
            raise PlaywrightSourceError(f"{where}: chain selector needs a non-empty 'steps' list")
        for i, step in enumerate(steps):
            validate_selector(step, f"{where}.steps[{i}]")
    if "nth" in spec and (not isinstance(spec["nth"], int) or spec["nth"] < 0):
        raise PlaywrightSourceError(f"{where}: 'nth' must be a non-negative integer")
    return spec


def validate_actions(actions):
    """Validate a browser action list; raises PlaywrightSourceError."""
    if not isinstance(actions, list) or not actions:
        raise PlaywrightSourceError("browser source needs a non-empty 'actions' list")
    for i, action in enumerate(actions):
        if not isinstance(action, dict):
            raise PlaywrightSourceError(f"browser action {i} must be an object")
        kind = action.get("action")
        if kind not in ACTION_TYPES:
            raise PlaywrightSourceError(f"browser action {i} has unknown action {kind!r}")
        if kind == "goto" and not isinstance(action.get("url"), str):
            raise PlaywrightSourceError(f"browser action {i} (goto) needs a 'url' string")
        if kind in ("click", "wait_for", "download", "fill"):
            validate_selector(action.get("selector"), f"browser action {i} ({kind})")
        if kind == "fill":
            if ("value" in action) == ("secret_env" in action):
                raise PlaywrightSourceError(
                    f"browser action {i} (fill) needs exactly one of 'value' or 'secret_env'"
                )
        if kind == "wait" and not isinstance(action.get("seconds"), (int, float)):
            raise PlaywrightSourceError(f"browser action {i} (wait) needs 'seconds'")
    if not any(a.get("action") == "download" for a in actions):
        raise PlaywrightSourceError("browser source needs a 'download' action to produce a file")
    return actions


# ------------------------------------------------------------------ source
class PlaywrightSource:
    """Drive a portal with Playwright (Chromium) and collect its downloads.

    ``page`` is injectable for tests; otherwise a real Chromium is
    launched here, used for one download() call, and closed before
    returning. Each download action saves its file into dest_dir
    via .part + rename.
    """

    def __init__(self, cfg: PlaywrightSourceConfig, page=None):
        self.cfg = cfg
        self.actions = validate_actions(list(cfg.actions))
        self.headless = cfg.headless
        self.timeout = cfg.timeout_seconds
        self.executable_path = cfg.executable_path
        self._page = page
        self._owns_page = page is None

    def _build_page(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise PlaywrightSourceError(
                "playwright source needs playwright: pip install playwright, "
                "then playwright install chromium"
            ) from e
        try:
            pw = sync_playwright().start()
        except Exception as e:
            raise PlaywrightSourceError(
                "playwright source could not start Playwright "
                f"(is Chromium installed? run `playwright install chromium`) ({e})"
            ) from e
        try:
            browser = pw.chromium.launch(
                headless=self.headless, executable_path=self.executable_path
            )
        except Exception as e:
            pw.stop()
            raise PlaywrightSourceError(
                "playwright source could not launch Chromium; if it is not "
                f"installed, run `playwright install chromium` ({e})"
            ) from e
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(self.timeout * 1000)
        return pw, browser, page

    def _run_action(self, page, action, dest_dir: Path, outputs: list[Path]):
        kind = action["action"]
        if kind == "goto":
            log.info("[playwright] goto %s", action["url"])
            page.goto(action["url"])
        elif kind == "fill":
            if "secret_env" in action:
                value = os.environ.get(action["secret_env"])
                if not value:
                    raise PlaywrightSourceError(
                        f"playwright fill: env var {action['secret_env']!r} is not set"
                    )
            else:
                value = action["value"]
            resolve_selector(page, action["selector"]).fill(value)
        elif kind == "click":
            log.info("[playwright] click %s", action["selector"])
            resolve_selector(page, action["selector"]).click()
        elif kind == "wait":
            page.wait_for_timeout(action["seconds"] * 1000)
        elif kind == "wait_for":
            resolve_selector(page, action["selector"]).wait_for(
                state="visible",
                timeout=int(action.get("timeout_seconds", 30)) * 1000,
            )
        elif kind == "download":
            log.info("[playwright] download via %s", action["selector"])
            locator = resolve_selector(page, action["selector"])
            timeout = int(action.get("timeout_seconds", 60))
            try:
                from playwright.sync_api import TimeoutError as _PWTimeoutError
            except ImportError:
                # No playwright installed (tests drive a fake page):
                # the builtin TimeoutError is what the fake raises.
                _PWTimeoutError = TimeoutError
            try:
                with page.expect_download(timeout=timeout * 1000) as dl:
                    locator.click()
                download = dl.value
            except (_PWTimeoutError, TimeoutError) as e:
                raise PlaywrightSourceError(
                    f"playwright download timed out after {timeout}s: {action['selector']!r}"
                ) from e
            name = download.suggested_filename or "download.bin"
            final = dest_dir / name
            tmp = dest_dir / (name + PART_SUFFIX)
            download.save_as(tmp)
            tmp.replace(final)
            outputs.append(final)
            log.info("[playwright] downloaded %s", name)

    def download(self, dest_dir: Path, run_id: str) -> list[Path]:
        dest_dir.mkdir(parents=True, exist_ok=True)
        outputs: list[Path] = []
        if self._page is not None:
            for action in self.actions:
                self._run_action(self._page, action, dest_dir, outputs)
        else:
            pw, browser, page = self._build_page()
            try:
                for action in self.actions:
                    self._run_action(page, action, dest_dir, outputs)
            finally:
                browser.close()
                pw.stop()
        if not outputs:
            raise PlaywrightSourceError("playwright source finished without a download")
        return outputs
