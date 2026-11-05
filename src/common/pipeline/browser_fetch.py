# FileFlow — browser source: fetch a file by driving a real web page.
#
# Some portals have no direct download URL: the file only appears after
# logging in and clicking through links and buttons. A "browser" source
# declares those steps as actions; the runner plays them back with
# Playwright (Chromium) and saves the resulting download.
#
# Playwright is a core dependency (see pyproject). The browser binary
# itself still needs one manual step: `playwright install chromium`.
# Without it, a browser source fails with a clear message instead of
# an obscure launch error.
#
# Action reference (each action is an object with an "action" field):
#   {"action": "goto", "url": "https://portal.example.com/login"}
#   {"action": "fill", "selector": "#username", "value": "alice"}
#   {"action": "fill", "selector": "#password", "secret": "PORTAL_PASSWORD"}
#       (secret = name of an environment variable; credentials never
#       belong in tasks.json — same rule as the other FileFlow secrets)
#   {"action": "click", "selector": "button[type=submit]"}
#   {"action": "wait", "seconds": 2}
#   {"action": "wait_for", "selector": "a.download", "timeout_seconds": 30}
#   {"action": "download", "selector": "a.download", "timeout_seconds": 60}
#       (clicks the selector and saves the browser download; this must
#       be the action that produces the file)
#
# The source object may also carry "headless": false to watch the run.
#
# ---------------------------------------------------------------- selectors
# A "selector" in fill/click/wait_for/download is either a plain CSS
# string (backward compatible) or a selector object. Objects pick an
# element by something other than CSS:
#
#   {"kind": "text", "text": "Download report"}   element by visible text
#   {"kind": "role", "role": "button", "name": "Sign in"}
#   {"kind": "label", "label": "Username"}          form field by its label
#   {"kind": "placeholder", "placeholder": "Search…"}
#   {"kind": "testid", "testid": "download-btn"}
#   {"kind": "xpath", "xpath": "//a[contains(@href, 'csv')]"}
#   {"kind": "css", "css": "a.download", "nth": 1}
#
# The table-row selector finds a row by a cell value, then (optionally)
# an element inside that row — the common "click download for the row
# titled X" case, which no plain CSS selector can express:
#
#   {"kind": "row", "table": "#files", "column": "Title",
#    "match": {"equals": "Q3 Report"}, "target": "a.download"}
#
#   "table": CSS for the table (optional; defaults to the first table)
#   "column": header name (matched case-insensitively) or 0-based index
#   "match": a string (exact match) or {"equals"|"contains"|"regex": ...}
#   "target": a selector resolved inside the matched row (optional; if
#     omitted, the row itself is the element)
#   "nth": which matching row, when several match (default 0)
#
# "chain" composes selectors: each step is resolved inside the previous
# one, so a row match can be combined with anything else:
#
#   {"kind": "chain", "steps": [
#     {"kind": "row", "table": "#files", "column": 0, "match": "Q3 Report"},
#     {"css": "a.download"}]}
#
# Leaf kinds also accept the shorthand form without "kind":
# {"text": "Download"} means {"kind": "text", "text": "Download"}.
#
# Extensibility: selector kinds live in SELECTOR_RESOLVERS, a plain dict
# of kind -> resolver(scope, spec) -> Locator. To add a kind, write a
# resolver and register it — no changes to the action runner needed:
#
#   from fileflow.infra.browser_fetch import register_selector
#   def by_data_action(scope, spec):
#       return scope.locator(f"[data-action='{spec['data_action']}']")
#   register_selector("data_action", by_data_action)
#
# A resolver receives a Playwright Page or Locator as scope, so it works
# inside "chain" and "row" targets too.
import os
import re

from playwright.sync_api import sync_playwright

from common.config import env_secret

ACTION_TYPES = ("goto", "fill", "click", "wait", "wait_for", "download")
_LEAF_KEYS = ("css", "text", "role", "label", "placeholder", "testid", "xpath")


class BrowserSourceError(Exception):
    pass


# ------------------------------------------------------------ selector core
def _norm(text):
    return " ".join((text or "").split())


def _match_text(text, match):
    """match is a string (exact) or {"equals"|"contains"|"regex": ...}."""
    if isinstance(match, str):
        match = {"equals": match}
    if not isinstance(match, dict):
        raise BrowserSourceError(f"row match must be a string or an object, got {match!r}")
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
    raise BrowserSourceError(f"row match needs one of equals/contains/regex, got {match!r}")


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
            raise BrowserSourceError(
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
        raise BrowserSourceError(
            f"row selector: no row matched {spec['match']!r} in column {column!r} "
            f"({len(seen_values)} rows checked; values seen: {seen_values[:5]})"
        )
    nth = spec.get("nth", 0)
    if nth >= len(matched):
        raise BrowserSourceError(
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
    raise BrowserSourceError(
        f"selector object needs a 'kind' (one of {sorted(SELECTOR_RESOLVERS)}), got {spec!r}"
    )


def resolve_selector(scope, spec):
    """Resolve a selector (CSS string or selector object) to a Locator."""
    if isinstance(spec, str):
        if not spec:
            raise BrowserSourceError("selector string must not be empty")
        return scope.locator(spec)
    if not isinstance(spec, dict):
        raise BrowserSourceError(f"selector must be a string or an object, got {spec!r}")
    kind = spec.get("kind") or _infer_kind(spec)
    resolver = SELECTOR_RESOLVERS.get(kind)
    if resolver is None:
        raise BrowserSourceError(
            f"unknown selector kind {kind!r}; known kinds: {sorted(SELECTOR_RESOLVERS)}"
        )
    locator = resolver(scope, spec)
    # "row" applies nth to its matched rows internally; every other kind
    # applies it to the resolved locator.
    if kind != "row" and "nth" in spec:
        locator = locator.nth(spec["nth"])
    return locator


def validate_selector(spec, where="selector"):
    """Validate a selector; raises BrowserSourceError on anything invalid."""
    if isinstance(spec, str):
        if not spec:
            raise BrowserSourceError(f"{where}: selector string must not be empty")
        return spec
    if not isinstance(spec, dict):
        raise BrowserSourceError(f"{where}: selector must be a string or an object")
    kind = spec.get("kind") or _infer_kind(spec)
    if kind not in SELECTOR_RESOLVERS:
        raise BrowserSourceError(
            f"{where}: unknown selector kind {kind!r}; known kinds: {sorted(SELECTOR_RESOLVERS)}"
        )
    if kind == "css" and not isinstance(spec.get("css"), str):
        raise BrowserSourceError(f"{where}: css selector needs a 'css' string")
    if kind == "text" and not isinstance(spec.get("text"), str):
        raise BrowserSourceError(f"{where}: text selector needs a 'text' string")
    if kind == "role" and not isinstance(spec.get("role"), str):
        raise BrowserSourceError(f"{where}: role selector needs a 'role' string")
    if kind == "label" and not isinstance(spec.get("label"), str):
        raise BrowserSourceError(f"{where}: label selector needs a 'label' string")
    if kind == "placeholder" and not isinstance(spec.get("placeholder"), str):
        raise BrowserSourceError(f"{where}: placeholder selector needs a 'placeholder' string")
    if kind == "testid" and not isinstance(spec.get("testid"), str):
        raise BrowserSourceError(f"{where}: testid selector needs a 'testid' string")
    if kind == "xpath" and not isinstance(spec.get("xpath"), str):
        raise BrowserSourceError(f"{where}: xpath selector needs an 'xpath' string")
    if kind == "row":
        if not isinstance(spec.get("column"), (int, str)):
            raise BrowserSourceError(f"{where}: row selector needs a 'column' (name or index)")
        match = spec.get("match")
        if not isinstance(match, (str, dict)):
            raise BrowserSourceError(f"{where}: row selector needs a 'match' (string or object)")
        if isinstance(match, dict) and not any(k in match for k in ("equals", "contains", "regex")):
            raise BrowserSourceError(f"{where}: row match needs one of equals/contains/regex")
        if "table" in spec:
            validate_selector(spec["table"], where + ".table")
        if "target" in spec:
            validate_selector(spec["target"], where + ".target")
    if kind == "chain":
        steps = spec.get("steps")
        if not isinstance(steps, list) or not steps:
            raise BrowserSourceError(f"{where}: chain selector needs a non-empty 'steps' list")
        for i, step in enumerate(steps):
            validate_selector(step, f"{where}.steps[{i}]")
    if "nth" in spec and (not isinstance(spec["nth"], int) or spec["nth"] < 0):
        raise BrowserSourceError(f"{where}: 'nth' must be a non-negative integer")
    return spec


def validate_actions(actions):
    """Validate a browser action list; raises BrowserSourceError."""
    if not isinstance(actions, list) or not actions:
        raise BrowserSourceError("browser source needs a non-empty 'actions' list")
    for i, action in enumerate(actions):
        if not isinstance(action, dict):
            raise BrowserSourceError(f"browser action {i} must be an object")
        kind = action.get("action")
        if kind not in ACTION_TYPES:
            raise BrowserSourceError(f"browser action {i} has unknown action {kind!r}")
        if kind == "goto" and not isinstance(action.get("url"), str):
            raise BrowserSourceError(f"browser action {i} (goto) needs a 'url' string")
        if kind in ("click", "wait_for", "download", "fill"):
            validate_selector(action.get("selector"), f"browser action {i} ({kind})")
        if kind == "fill":
            if ("value" in action) == ("secret" in action):
                raise BrowserSourceError(
                    f"browser action {i} (fill) needs exactly one of 'value' or 'secret'"
                )
        if kind == "wait" and not isinstance(action.get("seconds"), (int, float)):
            raise BrowserSourceError(f"browser action {i} (wait) needs 'seconds'")
    if not any(a.get("action") == "download" for a in actions):
        raise BrowserSourceError("browser source needs a 'download' action to produce a file")
    return actions


def run_browser_source(source, dest_dir, log):
    """Play back the source's actions; returns (local_path, name, last_url).

    dest_dir is the run workdir's input folder. Raises BrowserSourceError
    when the Chromium binary is missing or no download happens.
    """
    actions = validate_actions(source.get("actions"))
    headless = source.get("headless", True)
    os.makedirs(dest_dir, exist_ok=True)
    downloaded = {}
    last_url = ""
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=headless)
            except Exception as e:  # noqa: BLE001 — missing binary, bad flags, ...
                raise BrowserSourceError(
                    "browser source could not launch Chromium; if it is not "
                    f"installed, run `playwright install chromium` ({e})"
                ) from e
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()
            try:
                for action in actions:
                    kind = action["action"]
                    if kind == "goto":
                        last_url = action["url"]
                        log(f"[browser] goto {last_url}")
                        page.goto(last_url)
                    elif kind == "fill":
                        if "secret" in action:
                            value = env_secret(action["secret"])
                            if not value:
                                raise BrowserSourceError(
                                    f"browser fill: env var {action['secret']!r} is not set"
                                )
                        else:
                            value = action["value"]
                        log(f"[browser] fill {action['selector']}")
                        resolve_selector(page, action["selector"]).fill(value)
                    elif kind == "click":
                        log(f"[browser] click {action['selector']}")
                        resolve_selector(page, action["selector"]).click()
                    elif kind == "wait":
                        page.wait_for_timeout(action["seconds"] * 1000)
                    elif kind == "wait_for":
                        resolve_selector(page, action["selector"]).wait_for(
                            state="visible",
                            timeout=int(action.get("timeout_seconds", 30)) * 1000,
                        )
                    elif kind == "download":
                        log(f"[browser] download via {action['selector']}")
                        locator = resolve_selector(page, action["selector"])
                        with page.expect_download(
                            timeout=int(action.get("timeout_seconds", 60)) * 1000
                        ) as dl:
                            locator.click()
                        download = dl.value
                        name = download.suggested_filename or "download.bin"
                        dest = os.path.join(dest_dir, name)
                        download.save_as(dest)
                        downloaded = {"path": dest, "name": name}
            finally:
                browser.close()
    except BrowserSourceError:
        raise
    except Exception as e:  # noqa: BLE001 — Playwright's own errors vary
        raise BrowserSourceError(
            "browser source could not start Playwright; if Chromium is not "
            f"installed, run `playwright install chromium` ({e})"
        ) from e
    if not downloaded:
        raise BrowserSourceError("browser source finished without a download")
    return downloaded["path"], downloaded["name"], last_url
