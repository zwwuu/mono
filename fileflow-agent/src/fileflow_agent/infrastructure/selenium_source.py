"""Infrastructure -- Selenium source: fetch files by driving a real web page.

Some portals have no direct download URL: the file only appears after
logging in and clicking through links and buttons. A "selenium" source
declares those steps as actions; the runner plays them back with
Selenium (Chrome) and saves the resulting downloads. This mirrors the
monorepo FileFlow browser source, which uses the same action language
on Playwright -- the actions and selectors below are the same shapes,
so a portal script written for one reads the same in the other.

Selenium is an optional dependency (``pip install selenium``) and
Chrome must be installed on the PC; Selenium Manager (selenium>=4.6)
finds the matching chromedriver itself. Without either, a selenium
source fails with a clear message instead of an obscure launch error.

Action reference (each action is an object with an "action" field):
  {"action": "goto", "url": "https://portal.example.com/login"}
  {"action": "fill", "selector": "#username", "value": "alice"}
  {"action": "fill", "selector": "#password", "secret": "PORTAL_PASSWORD"}
      (secret = name of an environment variable; credentials never
      belong in the config file -- same rule as the other secrets)
  {"action": "click", "selector": "button[type=submit]"}
  {"action": "wait", "seconds": 2}
  {"action": "wait_for", "selector": "a.download", "timeout_seconds": 30}
  {"action": "download", "selector": "a.download", "timeout_seconds": 60}
      (clicks the selector and saves the browser download; every
      download action produces one file in the batch)

The source object may also carry "headless": false to watch the run,
"chrome_binary": "/path/to/chrome" for a non-standard install, and
"timeout_seconds" (default 60) for page loads.

Selectors work exactly as in the monorepo browser source: a plain CSS
string, or an object with a "kind": css, text, role, label,
placeholder, testid, xpath, row, chain. Selector kinds live in
SELECTOR_RESOLVERS, a plain dict of kind -> resolver(scope, spec) ->
element. To add a kind, write a resolver and register it -- no
changes to the action runner needed:

  from fileflow_agent.infrastructure.selenium_source import register_selector
  def by_data_action(scope, spec):
      return scope.find_element(By.CSS_SELECTOR, f"[data-action='{spec['data_action']}']")
  register_selector("data_action", by_data_action)

A resolver receives the driver or an element as scope, so it works
inside "chain" and "row" targets too.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path

from .filesystem import PART_SUFFIX, log

try:
    from selenium.webdriver.common.by import By
except ImportError:  # pragma: no cover -- selenium not installed
    # The same string constants, so this module (and its tests) import
    # without selenium; only building a real driver needs the package.

    class By:  # type: ignore[no-redef]
        CSS_SELECTOR = "css selector"
        XPATH = "xpath"
        ID = "id"
        NAME = "name"
        CLASS_NAME = "class name"
        TAG_NAME = "tag name"
        LINK_TEXT = "link text"
        PARTIAL_LINK_TEXT = "partial link text"


ACTION_TYPES = ("goto", "fill", "click", "wait", "wait_for", "download")
_LEAF_KEYS = ("css", "text", "role", "label", "placeholder", "testid", "xpath")
_DOWNLOAD_TEMPS = (".crdownload", ".part", ".tmp")


class SeleniumSourceError(Exception):
    pass


# ------------------------------------------------------------ selector core
def _norm(text):
    return " ".join((text or "").split())


def _xpath_literal(s: str) -> str:
    """An XPath string literal for s, whatever quotes it contains."""
    if '"' not in s:
        return f'"{s}"'
    if "'" not in s:
        return f"'{s}'"
    parts = s.split("'")
    return "concat(" + ', "\'", '.join(f'"{p}"' for p in parts) + ")"


def _match_text(text, match):
    """match is a string (exact) or {"equals"|"contains"|"regex": ...}."""
    if isinstance(match, str):
        match = {"equals": match}
    if not isinstance(match, dict):
        raise SeleniumSourceError(f"row match must be a string or an object, got {match!r}")
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
    raise SeleniumSourceError(f"row match needs one of equals/contains/regex, got {match!r}")


def _pick(elements, nth, what):
    if nth >= len(elements):
        raise SeleniumSourceError(
            f"selector {what!r}: {len(elements)} element(s) found but nth={nth} is out of range"
        )
    return elements[nth]


def _resolve_css(scope, spec):
    return _pick(scope.find_elements(By.CSS_SELECTOR, spec["css"]), spec.get("nth", 0), spec)


def _resolve_xpath(scope, spec):
    return _pick(scope.find_elements(By.XPATH, spec["xpath"]), spec.get("nth", 0), spec)


def _resolve_text(scope, spec):
    text = spec["text"]
    # Match on a direct text node, so an ancestor that merely contains
    # the text is not picked instead of the element that holds it.
    if spec.get("exact", False):
        xpath = f".//*[text()[normalize-space() = {_xpath_literal(text)}]]"
    else:
        xpath = f".//*[text()[contains(normalize-space(), {_xpath_literal(text)})]]"
    return _pick(scope.find_elements(By.XPATH, xpath), spec.get("nth", 0), spec)


def _resolve_testid(scope, spec):
    xpath = f".//*[@data-testid = {_xpath_literal(spec['testid'])}]"
    return _pick(scope.find_elements(By.XPATH, xpath), spec.get("nth", 0), spec)


def _resolve_placeholder(scope, spec):
    value = spec["placeholder"]
    if spec.get("exact", False):
        xpath = f".//*[@placeholder = {_xpath_literal(value)}]"
    else:
        xpath = f".//*[contains(@placeholder, {_xpath_literal(value)})]"
    return _pick(scope.find_elements(By.XPATH, xpath), spec.get("nth", 0), spec)


def _accessible_name(el) -> str:
    return _norm(el.get_attribute("aria-label") or el.text)


def _resolve_role(scope, spec):
    elements = scope.find_elements(By.XPATH, f".//*[@role = {_xpath_literal(spec['role'])}]")
    name = spec.get("name")
    if name is not None:
        op = "equals" if spec.get("exact", False) else "contains"
        elements = [el for el in elements if _match_text(_accessible_name(el), {op: name})]
    return _pick(elements, spec.get("nth", 0), spec)


def _resolve_label(scope, spec):
    label = spec["label"]
    if spec.get("exact", False):
        xpath = f".//label[normalize-space() = {_xpath_literal(label)}]"
    else:
        xpath = f".//label[contains(normalize-space(), {_xpath_literal(label)})]"
    labels = scope.find_elements(By.XPATH, xpath)
    label_el = _pick(labels, spec.get("nth", 0), spec)
    target = label_el.get_attribute("for")
    if target:
        return scope.find_element(By.ID, target)
    controls = label_el.find_elements(By.XPATH, ".//input | .//textarea | .//select")
    return _pick(controls, 0, spec)


def _resolve_row(scope, spec):
    table_spec = spec.get("table")
    if table_spec:
        table = resolve_selector(scope, table_spec)
    else:
        table = _pick(scope.find_elements(By.TAG_NAME, "table"), 0, "table")
    rows = table.find_elements(By.XPATH, ".//tbody/tr")
    if not rows:
        rows = table.find_elements(By.XPATH, ".//tr")
    column = spec["column"]
    if isinstance(column, int):
        idx = column
    else:
        headers = [_norm(th.text) for th in table.find_elements(By.XPATH, ".//thead/th")]
        if not headers:
            first_rows = table.find_elements(By.XPATH, ".//tr")
            if first_rows:
                headers = [_norm(th.text) for th in first_rows[0].find_elements(By.XPATH, ".//th")]
        wanted = _norm(str(column)).lower()
        idx = None
        for i, header in enumerate(headers):
            if header.lower() == wanted:
                idx = i
                break
        if idx is None:
            raise SeleniumSourceError(
                f"row selector: no column header {column!r}; headers are {headers}"
            )
    matched = []
    seen_values = []
    for row in rows:
        cells = row.find_elements(By.XPATH, "./td")
        if idx >= len(cells):
            continue
        text = _norm(cells[idx].text)
        seen_values.append(text)
        if _match_text(text, spec["match"]):
            matched.append(row)
    if not matched:
        raise SeleniumSourceError(
            f"row selector: no row matched {spec['match']!r} in column {column!r} "
            f"({len(seen_values)} rows checked; values seen: {seen_values[:5]})"
        )
    nth = spec.get("nth", 0)
    if nth >= len(matched):
        raise SeleniumSourceError(
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
    """Add (or replace) a selector kind. resolver(scope, spec) -> element."""
    SELECTOR_RESOLVERS[kind] = resolver


def _infer_kind(spec):
    present = [k for k in _LEAF_KEYS if k in spec]
    if len(present) == 1:
        return present[0]
    raise SeleniumSourceError(
        f"selector object needs a 'kind' (one of {sorted(SELECTOR_RESOLVERS)}), got {spec!r}"
    )


def resolve_selector(scope, spec):
    """Resolve a selector (CSS string or selector object) to one element."""
    if isinstance(spec, str):
        if not spec:
            raise SeleniumSourceError("selector string must not be empty")
        return _pick(scope.find_elements(By.CSS_SELECTOR, spec), 0, spec)
    if not isinstance(spec, dict):
        raise SeleniumSourceError(f"selector must be a string or an object, got {spec!r}")
    kind = spec.get("kind") or _infer_kind(spec)
    resolver = SELECTOR_RESOLVERS.get(kind)
    if resolver is None:
        raise SeleniumSourceError(
            f"unknown selector kind {kind!r}; known kinds: {sorted(SELECTOR_RESOLVERS)}"
        )
    return resolver(scope, spec)


def validate_selector(spec, where="selector"):
    """Validate a selector; raises SeleniumSourceError on anything invalid."""
    if isinstance(spec, str):
        if not spec:
            raise SeleniumSourceError(f"{where}: selector string must not be empty")
        return spec
    if not isinstance(spec, dict):
        raise SeleniumSourceError(f"{where}: selector must be a string or an object")
    kind = spec.get("kind") or _infer_kind(spec)
    if kind not in SELECTOR_RESOLVERS:
        raise SeleniumSourceError(
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
            raise SeleniumSourceError(f"{where}: {key} selector needs a '{label}' string")
    if kind == "row":
        if not isinstance(spec.get("column"), (int, str)):
            raise SeleniumSourceError(f"{where}: row selector needs a 'column' (name or index)")
        match = spec.get("match")
        if not isinstance(match, (str, dict)):
            raise SeleniumSourceError(f"{where}: row selector needs a 'match' (string or object)")
        if isinstance(match, dict) and not any(k in match for k in ("equals", "contains", "regex")):
            raise SeleniumSourceError(f"{where}: row match needs one of equals/contains/regex")
        if "table" in spec:
            validate_selector(spec["table"], where + ".table")
        if "target" in spec:
            validate_selector(spec["target"], where + ".target")
    if kind == "chain":
        steps = spec.get("steps")
        if not isinstance(steps, list) or not steps:
            raise SeleniumSourceError(f"{where}: chain selector needs a non-empty 'steps' list")
        for i, step in enumerate(steps):
            validate_selector(step, f"{where}.steps[{i}]")
    if "nth" in spec and (not isinstance(spec["nth"], int) or spec["nth"] < 0):
        raise SeleniumSourceError(f"{where}: 'nth' must be a non-negative integer")
    return spec


def validate_actions(actions):
    """Validate a browser action list; raises SeleniumSourceError."""
    if not isinstance(actions, list) or not actions:
        raise SeleniumSourceError("browser source needs a non-empty 'actions' list")
    for i, action in enumerate(actions):
        if not isinstance(action, dict):
            raise SeleniumSourceError(f"browser action {i} must be an object")
        kind = action.get("action")
        if kind not in ACTION_TYPES:
            raise SeleniumSourceError(f"browser action {i} has unknown action {kind!r}")
        if kind == "goto" and not isinstance(action.get("url"), str):
            raise SeleniumSourceError(f"browser action {i} (goto) needs a 'url' string")
        if kind in ("click", "wait_for", "download", "fill"):
            validate_selector(action.get("selector"), f"browser action {i} ({kind})")
        if kind == "fill":
            if ("value" in action) == ("secret" in action):
                raise SeleniumSourceError(
                    f"browser action {i} (fill) needs exactly one of 'value' or 'secret'"
                )
        if kind == "wait" and not isinstance(action.get("seconds"), (int, float)):
            raise SeleniumSourceError(f"browser action {i} (wait) needs 'seconds'")
    if not any(a.get("action") == "download" for a in actions):
        raise SeleniumSourceError("browser source needs a 'download' action to produce a file")
    return actions


# ------------------------------------------------------------------ source
class SeleniumSource:
    """Drive a portal with Selenium (Chrome) and collect its downloads.

    ``driver`` is injectable for tests; otherwise a real Chrome is
    built here, used for one download() call, and quit before
    returning. Each download action moves its file out of the
    browser's download dir into dest_dir via .part + rename.
    """

    def __init__(self, cfg: dict, driver=None):
        self.cfg = cfg
        self.actions = validate_actions(cfg.get("actions"))
        self.headless = cfg.get("headless", True)
        self.timeout = cfg.get("timeout_seconds", 60)
        self.chrome_binary = cfg.get("chrome_binary")
        self._driver = driver
        self._owns_driver = driver is None

    def _build_driver(self, download_dir: Path):
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
        except ImportError as e:
            raise SeleniumSourceError("selenium source needs selenium: pip install selenium") from e
        options = Options()
        if self.headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        if self.chrome_binary:
            options.binary_location = self.chrome_binary
        options.add_experimental_option(
            "prefs",
            {
                "download.default_directory": str(download_dir.resolve()),
                "download.prompt_for_download": False,
                "safebrowsing.enabled": True,
            },
        )
        try:
            driver = webdriver.Chrome(options=options)
        except Exception as e:
            raise SeleniumSourceError(
                f"selenium source could not launch Chrome; is Chrome installed on this PC? ({e})"
            ) from e
        driver.set_page_load_timeout(self.timeout)
        return driver

    def _wait_for(self, driver, spec, timeout: int):
        """Resolve the selector until its element is visible."""
        deadline = time.monotonic() + timeout
        last_error = None
        while True:
            try:
                element = resolve_selector(driver, spec)
                if element.is_displayed():
                    return element
            except Exception as e:  # noqa: BLE001 -- a wait polls through transient driver errors
                last_error = e
            if time.monotonic() >= deadline:
                raise SeleniumSourceError(
                    f"selenium wait_for timed out after {timeout}s: {spec!r} ({last_error})"
                )
            time.sleep(0.5)

    def _wait_for_download(self, browser_dir: Path, before: set[str], timeout: int) -> str:
        """Wait for a new finished download in browser_dir; return its name.

        A file counts once it is not a temp download (.crdownload and
        friends) and its size is unchanged across two polls -- the
        browser is done writing it.
        """
        deadline = time.monotonic() + timeout
        last_sizes: dict[str, int] = {}
        while True:
            names = {p.name for p in browser_dir.iterdir()}
            temps = {n for n in names if n.endswith(_DOWNLOAD_TEMPS)}
            new = {n for n in names - before if n not in temps}
            if new and not temps:
                try:
                    sizes = {n: (browser_dir / n).stat().st_size for n in new}
                except OSError:
                    sizes = {}  # a file vanished mid-poll; keep waiting
                if sizes and sizes == last_sizes:
                    return max(new, key=lambda n: (browser_dir / n).stat().st_mtime)
                last_sizes = sizes
            if time.monotonic() >= deadline:
                raise SeleniumSourceError(
                    f"selenium download timed out after {timeout}s (saw: {sorted(names - before)})"
                )
            time.sleep(0.5)

    def _run_action(self, driver, action, browser_dir: Path, dest_dir: Path, outputs: list[Path]):
        kind = action["action"]
        if kind == "goto":
            log.info("[selenium] goto %s", action["url"])
            driver.get(action["url"])
        elif kind == "fill":
            if "secret" in action:
                value = os.environ.get(action["secret"])
                if not value:
                    raise SeleniumSourceError(
                        f"selenium fill: env var {action['secret']!r} is not set"
                    )
            else:
                value = action["value"]
            element = resolve_selector(driver, action["selector"])
            element.clear()
            element.send_keys(value)
        elif kind == "click":
            log.info("[selenium] click %s", action["selector"])
            resolve_selector(driver, action["selector"]).click()
        elif kind == "wait":
            time.sleep(action["seconds"])
        elif kind == "wait_for":
            self._wait_for(driver, action["selector"], int(action.get("timeout_seconds", 30)))
        elif kind == "download":
            log.info("[selenium] download via %s", action["selector"])
            before = {p.name for p in browser_dir.iterdir()}
            resolve_selector(driver, action["selector"]).click()
            name = self._wait_for_download(
                browser_dir, before, int(action.get("timeout_seconds", 60))
            )
            src = browser_dir / name
            final = dest_dir / name
            tmp = dest_dir / (name + PART_SUFFIX)
            shutil.copyfile(src, tmp)
            tmp.replace(final)
            src.unlink(missing_ok=True)
            outputs.append(final)
            log.info("[selenium] downloaded %s", name)

    def download(self, dest_dir: Path, run_id: str) -> list[Path]:
        dest_dir.mkdir(parents=True, exist_ok=True)
        browser_dir = dest_dir / ".downloads"
        browser_dir.mkdir(parents=True, exist_ok=True)
        driver = self._driver or self._build_driver(browser_dir)
        outputs: list[Path] = []
        try:
            for action in self.actions:
                self._run_action(driver, action, browser_dir, dest_dir, outputs)
        finally:
            if self._owns_driver:
                try:
                    driver.quit()
                except Exception as _:  # noqa: BLE001 -- quit() must never raise
                    log.debug("selenium driver quit failed (ignored): %s", _)
            shutil.rmtree(browser_dir, ignore_errors=True)
        if not outputs:
            raise SeleniumSourceError("selenium source finished without a download")
        return outputs
