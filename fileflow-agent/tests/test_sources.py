"""Tests for the remote sources: remote-dir (FTP/SFTP/local), SMB, Selenium.

The real backends are replaced with fakes: a LocalTransport stands in
for the remote server, a tmp-dir-backed object stands in for smbclient,
and a tiny in-memory DOM stands in for Chrome. The selection rules
(pattern, .part, still-being-written, empty) and the action/selector
machinery are the same either way; only the transport differs.
"""

import os
import re
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fileflow_agent.adapters.cli import build_download_use_case
from fileflow_agent.domain.integrity import render_template, template_context
from fileflow_agent.infrastructure.config import (
    PlaywrightSourceConfig,
    SeleniumSourceConfig,
    parse_config,
    parse_source,
    parse_transport,
)
from fileflow_agent.infrastructure.playwright_source import (
    PlaywrightSource,
    PlaywrightSourceError,
    _match_text,
    _norm,
)
from fileflow_agent.infrastructure.playwright_source import (
    resolve_selector as resolve_selector_playwright,
)
from fileflow_agent.infrastructure.selenium_source import (
    SeleniumSource,
    SeleniumSourceError,
    resolve_selector,
)
from fileflow_agent.infrastructure.smb_source import SmbSource
from fileflow_agent.infrastructure.sources import (
    RemoteDirectorySource,
    RemoteFilesSource,
)
from fileflow_agent.infrastructure.transport import resolve_password


def _run_once(cfg: dict) -> dict:
    use_case, transport, repository = build_download_use_case(parse_config(cfg))
    try:
        return use_case.run()
    finally:
        transport.close()
        repository.close()


def _make_remote(root: Path, files: dict[str, str], old: bool = True) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (root / name).write_text(content, encoding="utf-8")
    if old:
        ancient = time.time() - 3600
        for p in root.iterdir():
            os.utime(p, (ancient, ancient))


# ------------------------------------------------------------- remote-dir
def test_remote_dir_selects_stable_files(tmp_path: Path):
    remote = tmp_path / "remote"
    _make_remote(
        remote,
        {"a.csv": "x,1\n", "b.csv": "x,2\n", "c.csv.part": "x,3\n", "empty.csv": ""},
    )
    fresh = remote / "fresh.csv"
    fresh.write_text("x,9\n", encoding="utf-8")  # young mtime: still being written
    source = RemoteDirectorySource(
        parse_source(
            {
                "type": "remote-dir",
                "transport": "local",
                "root": str(remote),
                "remote_dir": ".",
                "pattern": "*.csv",
                "min_age_seconds": 2,
            }
        )
    )
    staged = source.download(tmp_path / "staging", "run-1")
    assert sorted(p.name for p in staged) == ["a.csv", "b.csv"]


def test_remote_dir_end_to_end_with_skip(tmp_path: Path):
    remote = tmp_path / "remote"
    _make_remote(remote, {"daily.csv": " id , amount \n 1 , 10.5 \n\n2,20\n"})
    cfg = {
        "job": "daily-report",
        "source": {
            "type": "remote-dir",
            "transport": "local",
            "root": str(remote),
            "remote_dir": ".",
            "pattern": "*.csv",
            "min_age_seconds": 0,
        },
        "expect_csv": True,
        "procedure": "normalize-csv",
        "rename_pattern": "{date}_{name}{ext}",
        "staging_dir": str(tmp_path / "staging"),
        "ledger_db": str(tmp_path / "agent.db"),
        "destination": {"transport": "local", "root": str(tmp_path / "ftp")},
    }
    assert _run_once(cfg)["status"] == "UPLOADED"
    assert _run_once(cfg)["status"] == "SKIPPED"  # second pass: already processed


# --------------------------------------------------------------------- smb
class FakeSmb:
    """smbclient-shaped, backed by a local folder. Records UNC paths seen."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.seen: list[str] = []

    def _name(self, unc: str) -> str:
        self.seen.append(unc)
        return unc.rsplit("\\", 1)[-1]

    def listdir(self, unc, username="", password=""):
        self.seen.append(unc)
        return sorted(p.name for p in self.root.iterdir())

    def stat(self, unc, username="", password=""):
        return (self.root / self._name(unc)).stat()

    def open_file(self, unc, mode="rb", username="", password=""):
        return open(self.root / self._name(unc), mode)


def test_smb_selects_stable_files(tmp_path: Path):
    share = tmp_path / "share"
    _make_remote(
        share,
        {"a.csv": "x,1\n", "b.csv": "x,2\n", "c.csv.part": "x,3\n", "empty.csv": ""},
    )
    client = FakeSmb(share)
    source = SmbSource(
        parse_source(
            {
                "type": "smb",
                "server": "fileserver",
                "share": "reports",
                "path": "daily",
                "pattern": "*.csv",
                "min_age_seconds": 2,
            }
        ),
        client=client,
    )
    staged = source.download(tmp_path / "staging", "run-1")
    assert sorted(p.name for p in staged) == ["a.csv", "b.csv"]
    assert client.seen[0] == "\\\\fileserver\\reports\\daily"


# --------------------------------------------------------------- selenium
class FakeEl:
    """A DOM element with just enough of Selenium's API for the tests."""

    def __init__(self, tag="div", text="", attrs=None, children=None):
        self.tag = tag
        self.text = text
        self._attrs = attrs or {}
        self.children = children or []
        self.root = self
        for child in self.children:
            for el in child._all():
                el.root = self
        self.clicked = False
        self.sent: list[str] = []
        self.cleared = False
        self.on_click = None

    def _all(self):
        yield self
        for child in self.children:
            yield from child._all()

    def _descendants(self):
        for child in self.children:
            yield child
            yield from child._descendants()

    def find_element(self, by, value):
        found = self.find_elements(by, value)
        if not found:
            raise LookupError(f"no element for {by}={value}")
        return found[0]

    def find_elements(self, by, value):
        if by == "id":
            return [el for el in self._all() if el._attrs.get("id") == value]
        if by == "tag name":
            return [el for el in self._all() if el.tag == value]
        if by == "css selector":
            return [el for el in self._all() if _css_matches(el, value)]
        if by == "xpath":
            return _xpath_find(self, value)
        return []

    def get_attribute(self, name):
        return self._attrs.get(name)

    def is_displayed(self):
        return True

    def click(self):
        self.clicked = True
        if self.on_click:
            self.on_click()

    def clear(self):
        self.cleared = True

    def send_keys(self, value):
        self.sent.append(value)


def _css_matches(el: FakeEl, selector: str) -> bool:
    """A small CSS matcher for the shapes the tests use:
    tag, #id, .class, [attr=value] (quoted or not), and combinations."""
    rest = selector
    tag = ident = cls = attr = val = None
    m = re.match(r"^(\w+|\*)", rest)
    if m:
        tag = m.group(1)
        rest = rest[m.end() :]
    m = re.match(r"^#([\w-]+)", rest)
    if m:
        ident = m.group(1)
        rest = rest[m.end() :]
    m = re.match(r"^\.([\w-]+)", rest)
    if m:
        cls = m.group(1)
        rest = rest[m.end() :]
    m = re.match(r"^\[([\w-]+)(?:=([\"']?)([^\]\"']*)\2)?\]", rest)
    if m:
        attr, val = m.group(1), m.group(3)
        rest = rest[m.end() :]
    if rest:
        return False
    if tag and tag != "*" and el.tag != tag:
        return False
    if ident and el._attrs.get("id") != ident:
        return False
    if cls and cls not in (el._attrs.get("class") or "").split():
        return False
    if attr and el._attrs.get(attr) != val:
        return False
    return True


def _xpath_find(scope: FakeEl, xpath: str):
    """A small XPath evaluator for the shapes the resolvers generate."""
    results: list[FakeEl] = []
    for part in xpath.split(" | "):
        results.extend(_xpath_one(scope, part.strip()))
    return results


def _xpath_one(scope: FakeEl, xpath: str):
    """One union branch: a path of tag[predicate] steps. The first step
    searches the pool the prefix selects (.// descendants, // the whole
    document, ./ direct children); later steps search direct children
    of the previous step's matches, as XPath does."""
    if xpath.startswith(".//"):
        current: list[FakeEl] = list(scope._descendants())
        rest = xpath[3:]
    elif xpath.startswith("//"):
        current = list(scope.root._all())
        rest = xpath[2:]
    elif xpath.startswith("./"):
        current = list(scope.children)
        rest = xpath[2:]
    else:
        return []
    for i, step in enumerate(rest.split("/")):
        if i > 0:
            current = [c for el in current for c in el.children]
        m = re.fullmatch(r"(\*|\w+)(?:\[(.*)\])?", step)
        if not m:
            return []
        tag, pred = m.groups()
        current = [
            el
            for el in current
            if (tag == "*" or el.tag == tag) and (pred is None or _pred_matches(el, pred))
        ]
    return current


def _pred_matches(el: FakeEl, pred: str) -> bool:
    text = el.text or ""
    norm = " ".join(text.split())
    m = re.fullmatch(r'text\(\)\[normalize-space\(\) = ("(?:[^"]*)"|\'(?:[^\']*)\')\]', pred)
    if m:
        return norm == m.group(1)[1:-1]
    m = re.fullmatch(
        r'text\(\)\[contains\(normalize-space\(\), ("(?:[^"]*)"|\'(?:[^\']*)\')\)\]', pred
    )
    if m:
        return m.group(1)[1:-1] in norm
    m = re.fullmatch(r'@([\w-]+) = ("(?:[^"]*)"|\'(?:[^\']*)\')', pred)
    if m:
        return (el._attrs.get(m.group(1)) or "") == m.group(2)[1:-1]
    m = re.fullmatch(r'contains\(@([\w-]+), ("(?:[^"]*)"|\'(?:[^\']*)\')\)', pred)
    if m:
        return m.group(2)[1:-1] in (el._attrs.get(m.group(1)) or "")
    m = re.fullmatch(r'normalize-space\(\) = ("(?:[^"]*)"|\'(?:[^\']*)\')', pred)
    if m:
        return norm == m.group(1)[1:-1]
    m = re.fullmatch(r'contains\(normalize-space\(\), ("(?:[^"]*)"|\'(?:[^\']*)\')\)', pred)
    if m:
        return m.group(1)[1:-1] in norm
    return False


class FakeDriver:
    def __init__(self, root: FakeEl):
        self.root = root
        self.visited: list[str] = []
        self.quit_called = False

    def get(self, url):
        self.visited.append(url)

    def find_element(self, by, value):
        return self.root.find_element(by, value)

    def find_elements(self, by, value):
        return self.root.find_elements(by, value)

    def quit(self):
        self.quit_called = True


def _portal_dom(download_dir: Path) -> FakeEl:
    link = FakeEl(
        "a",
        text="Download report",
        attrs={"class": "download", "href": "/reports/daily.csv"},
    )
    link.on_click = lambda: (download_dir / "report.csv").write_text("x,1\n", encoding="utf-8")
    return FakeEl(
        "html",
        children=[
            FakeEl(
                "body",
                children=[
                    FakeEl("input", attrs={"id": "username"}),
                    FakeEl("input", attrs={"id": "password"}),
                    FakeEl("button", text="Sign in", attrs={"type": "submit"}),
                    link,
                ],
            )
        ],
    )


def test_selenium_action_flow(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PORTAL_PASSWORD", "s3cret")
    staging = tmp_path / "staging"
    driver = FakeDriver(_portal_dom(staging / ".downloads"))
    source = SeleniumSource(
        parse_source(
            {
                "type": "selenium",
                "actions": [
                    {"action": "goto", "url": "https://portal.example.com/login"},
                    {"action": "fill", "selector": "#username", "value": "alice"},
                    {"action": "fill", "selector": "#password", "secret_env": "PORTAL_PASSWORD"},
                    {"action": "click", "selector": "button[type=submit]"},
                    {"action": "wait_for", "selector": "a.download", "timeout_seconds": 5},
                    {"action": "download", "selector": "a.download", "timeout_seconds": 5},
                ],
            }
        ),
        driver=driver,
    )
    staged = source.download(staging, "run-1")
    assert [p.name for p in staged] == ["report.csv"]
    assert staged[0].read_text() == "x,1\n"
    assert not (staging / ".downloads").exists()  # browser dir cleaned up
    assert driver.visited == ["https://portal.example.com/login"]
    username = driver.root.find_element("css selector", "#username")
    assert username.sent == ["alice"] and username.cleared
    password = driver.root.find_element("css selector", "#password")
    assert password.sent == ["s3cret"]


def test_selenium_validations():
    with pytest.raises(SeleniumSourceError, match="non-empty 'actions' list"):
        SeleniumSource(SeleniumSourceConfig(actions=()))
    with pytest.raises(SeleniumSourceError, match="needs a 'download' action"):
        SeleniumSource(SeleniumSourceConfig(actions=({"action": "goto", "url": "https://x"},)))
    with pytest.raises(SeleniumSourceError, match="unknown action"):
        SeleniumSource(
            SeleniumSourceConfig(
                actions=(
                    {"action": "teleport", "url": "https://x"},
                    {"action": "download", "selector": "a"},
                )
            )
        )
    with pytest.raises(SeleniumSourceError, match="unknown selector kind"):
        SeleniumSource(
            SeleniumSourceConfig(
                actions=({"action": "download", "selector": {"kind": "aura", "aura": "x"}},)
            )
        )


def test_selenium_unset_secret_env(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("PORTAL_PASSWORD", raising=False)
    driver = FakeDriver(_portal_dom(tmp_path / ".downloads"))
    source = SeleniumSource(
        parse_source(
            {
                "type": "selenium",
                "actions": [
                    {"action": "fill", "selector": "#password", "secret_env": "PORTAL_PASSWORD"},
                    {"action": "download", "selector": "a.download"},
                ],
            }
        ),
        driver=driver,
    )
    with pytest.raises(SeleniumSourceError, match="env var 'PORTAL_PASSWORD' is not set"):
        source.download(tmp_path / "staging", "run-1")


def test_selenium_download_timeout(tmp_path: Path):
    link = FakeEl("a", text="Download", attrs={"class": "download"})
    driver = FakeDriver(FakeEl("html", children=[FakeEl("body", children=[link])]))
    source = SeleniumSource(
        parse_source(
            {
                "type": "selenium",
                "actions": [{"action": "download", "selector": "a.download", "timeout_seconds": 1}],
            }
        ),
        driver=driver,
    )
    with pytest.raises(SeleniumSourceError, match="download timed out"):
        source.download(tmp_path / "staging", "run-1")


def _table_dom() -> FakeEl:
    def row(title, size):
        return FakeEl(
            "tr",
            children=[
                FakeEl("td", text=title),
                FakeEl("td", text=size),
                FakeEl("td", children=[FakeEl("a", text="Download", attrs={"class": "download"})]),
            ],
        )

    return FakeEl(
        "html",
        children=[
            FakeEl(
                "body",
                children=[
                    FakeEl(
                        "table",
                        attrs={"id": "files"},
                        children=[
                            FakeEl(
                                "thead",
                                children=[
                                    FakeEl(
                                        "tr",
                                        children=[
                                            FakeEl("th", text="Title"),
                                            FakeEl("th", text="Size"),
                                            FakeEl("th", text=""),
                                        ],
                                    )
                                ],
                            ),
                            FakeEl(
                                "tbody", children=[row("Q3 Report", "10"), row("Q4 Report", "20")]
                            ),
                        ],
                    ),
                    FakeEl("input", attrs={"placeholder": "Search…"}),
                    FakeEl("button", text="Export", attrs={"data-testid": "export-btn"}),
                ],
            )
        ],
    )


def test_selenium_selectors():
    root = _table_dom()
    driver = FakeDriver(root)
    link = resolve_selector(
        driver,
        {
            "kind": "row",
            "table": "#files",
            "column": "Title",
            "match": "Q3 Report",
            "target": "a.download",
        },
    )
    assert link.tag == "a" and link.text == "Download"
    # chain: same row match, then the link inside it
    chained = resolve_selector(
        driver,
        {
            "kind": "chain",
            "steps": [
                {"kind": "row", "table": "#files", "column": 0, "match": {"contains": "Q4"}},
                {"css": "a.download"},
            ],
        },
    )
    assert chained.tag == "a"
    assert resolve_selector(driver, {"kind": "text", "text": "Export"}).tag == "button"
    assert resolve_selector(driver, {"kind": "testid", "testid": "export-btn"}).tag == "button"
    assert resolve_selector(driver, {"kind": "placeholder", "placeholder": "Search"}).tag == "input"
    with pytest.raises(SeleniumSourceError, match="no row matched"):
        resolve_selector(
            driver,
            {"kind": "row", "table": "#files", "column": "Title", "match": "Q9 Report"},
        )


# ------------------------------------------------------------ remote-files
def test_render_template_and_context():
    ctx = template_context("ftp-inbox", "sales")
    assert render_template("inbox/{year}{month}", ctx) == (f"inbox/{datetime.now():%Y%m}")
    assert render_template("Test3-{category}.csv", ctx) == "Test3-sales.csv"
    assert render_template("{job}-{day}.csv", ctx) == (f"ftp-inbox-{datetime.now():%d}.csv")
    with pytest.raises(ValueError, match="unknown token"):
        render_template("Test-{nope}.csv", ctx)
    assert "category" not in template_context("ftp-inbox")


def test_resolve_password_env_wins(monkeypatch):
    cfg = parse_transport(
        {"transport": "ftp", "host": "h", "password_env": "APP_FTP_PASSWORD", "password": "literal"}
    )
    monkeypatch.setenv("APP_FTP_PASSWORD", "from-env")
    assert resolve_password(cfg) == "from-env"
    monkeypatch.delenv("APP_FTP_PASSWORD")
    assert resolve_password(cfg) == "literal"
    assert resolve_password(parse_transport({"transport": "local", "root": "/tmp"})) == ""


def _remote_files_cfg(remote: Path) -> tuple[dict, dict]:
    now = datetime.now()
    d1 = remote / "inbox" / f"{now:%Y%m}"
    d1.mkdir(parents=True)
    (d1 / f"Test1-{now:%Y}-{now:%m}-{now:%d}.csv").write_text("a,1\n", encoding="utf-8")
    d2 = remote / "inbox" / f"{now:%Y}"
    d2.mkdir(parents=True)
    (d2 / f"Test2-{now:%Y}-{now:%m}-{now:%d}.csv").write_text("b,2\n", encoding="utf-8")
    d3 = remote / "inbox" / "sales"
    d3.mkdir(parents=True)
    (d3 / "Test3-sales.csv").write_text("c,3\n", encoding="utf-8")
    for p in remote.rglob("*.csv"):
        os.utime(p, (time.time() - 3600, time.time() - 3600))
    source_cfg = {
        "type": "remote-files",
        "transport": "local",
        "root": str(remote),
        "min_age_seconds": 5,
        "files": [
            {"directory": "inbox/{year}{month}", "name": "Test1-{year}-{month}-{day}.csv"},
            {"directory": "inbox/{year}", "name": "Test2-{year}-{month}-{day}.csv"},
            {"directory": "inbox/{category}", "name": "Test3-{category}.csv"},
        ],
    }
    return source_cfg, {"job": "ftp-inbox", "category": "sales"}


def test_remote_files_downloads_named_files(tmp_path: Path):
    source_cfg, job_cfg = _remote_files_cfg(tmp_path / "remote")
    source = RemoteFilesSource(parse_source(source_cfg), job_cfg["job"], job_cfg.get("category"))
    staged = source.download(tmp_path / "staging", "run-1")
    now = datetime.now()
    assert sorted(p.name for p in staged) == [
        f"Test1-{now:%Y}-{now:%m}-{now:%d}.csv",
        f"Test2-{now:%Y}-{now:%m}-{now:%d}.csv",
        "Test3-sales.csv",
    ]


def test_remote_files_missing_file_fails_loud(tmp_path: Path):
    source_cfg, job_cfg = _remote_files_cfg(tmp_path / "remote")
    source_cfg["files"] = [{"directory": "inbox/{year}{month}", "name": "Nope.csv"}]
    with pytest.raises(ValueError, match="file not found"):
        RemoteFilesSource(
            parse_source(source_cfg), job_cfg["job"], job_cfg.get("category")
        ).download(tmp_path / "staging", "run-1")


def test_remote_files_too_young_fails_loud(tmp_path: Path):
    remote = tmp_path / "remote"
    (remote / "inbox").mkdir(parents=True)
    fresh = remote / "inbox" / "new.csv"
    fresh.write_text("x,1\n", encoding="utf-8")  # mtime is now: still being written
    source = RemoteFilesSource(
        parse_source(
            {
                "type": "remote-files",
                "transport": "local",
                "root": str(remote),
                "min_age_seconds": 300,
                "files": [{"directory": "inbox", "name": "new.csv"}],
            }
        ),
        "ftp-inbox",
    )
    with pytest.raises(ValueError, match="still being written"):
        source.download(tmp_path / "staging", "run-1")


def test_remote_files_end_to_end_direct_drop(tmp_path: Path):
    source_cfg, _job_cfg = _remote_files_cfg(tmp_path / "remote")
    now = datetime.now()
    cfg = {
        "job": "ftp-inbox",
        "category": "sales",
        "source": source_cfg,
        "expect_csv": True,
        "procedure": "normalize-csv",
        "rename_pattern": "{date}_{name}{ext}",
        "staging_dir": str(tmp_path / "staging"),
        "ledger_db": str(tmp_path / "agent.db"),
        "destination": {
            "transport": "local",
            "root": str(tmp_path / "dest"),
            "directory": "inbox/{year}{month}",
        },
    }
    assert _run_once(cfg)["status"] == "UPLOADED"
    dest_dir = tmp_path / "dest" / "inbox" / f"{now:%Y%m}"
    assert len(list(dest_dir.glob("*.manifest.json"))) == 1
    assert len(list(dest_dir.glob("*.csv"))) == 3
    assert not list((tmp_path / "dest").glob("incoming"))  # no watcher incoming dir
    assert _run_once(cfg)["status"] == "SKIPPED"  # second pass: already processed


# --------------------------------------------------------------- playwright
class FakeDownload:
    """A browser download the fake page hands to the source."""

    def __init__(self, name, content):
        self.suggested_filename = name
        self._content = content

    def save_as(self, path):
        data = self._content.encode() if isinstance(self._content, str) else self._content
        Path(path).write_bytes(data)


class _FakeExpectDownload:
    def __init__(self, page):
        self.page = page

    def __enter__(self):
        self.before = len(self.page.created_downloads)
        return self

    @property
    def value(self):
        if len(self.page.created_downloads) <= self.before:
            raise TimeoutError("playwright download timed out")
        return self.page.created_downloads[-1]

    def __exit__(self, *exc):
        return False


def _css_find(roots, selector):
    """Descendant CSS matching for the shapes the tests use: a
    space-separated path of single compound selectors."""
    current = list(roots)
    for part in selector.split():
        nxt = []
        for el in current:
            nxt.extend([d for d in el._descendants() if _css_matches(d, part)])
        current = nxt
    return current


def _scope_find(roots, selector):
    if selector.startswith("xpath="):
        found = []
        for el in roots:
            found.extend(_xpath_find(el, selector[len("xpath=") :]))
        return found
    return _css_find(roots, selector)


class FakeLocator:
    """A Playwright locator over the same in-memory DOM as FakeEl."""

    def __init__(self, elements, page):
        self.elements = list(elements)
        self.page = page

    def _first(self):
        if not self.elements:
            raise LookupError("locator matched no element")
        return self.elements[0]

    def locator(self, selector):
        return FakeLocator(_scope_find(self.elements, selector), self.page)

    def nth(self, i):
        return FakeLocator(self.elements[i : i + 1], self.page)

    @property
    def first(self):
        return self.nth(0)

    def count(self):
        return len(self.elements)

    def all_inner_texts(self):
        return [el.text for el in self.elements]

    def inner_text(self):
        return self._first().text

    def fill(self, value):
        self._first().sent.append(value)

    def click(self):
        self._first().click()

    def wait_for(self, state="visible", timeout=None):
        self._first()

    def get_by_text(self, text, exact=False):
        norm = _norm(text)
        return FakeLocator(
            [
                el
                for el in _all_descendants(self.elements)
                if (_norm(el.text) == norm if exact else norm in _norm(el.text))
            ],
            self.page,
        )

    def get_by_role(self, role, name=None, exact=False):
        els = [el for el in _all_descendants(self.elements) if el._attrs.get("role") == role]
        if name is not None:
            op = "equals" if exact else "contains"
            els = [el for el in els if _match_text(_accessible_name(el), {op: name})]
        return FakeLocator(els, self.page)

    def get_by_label(self, label, exact=False):
        norm = _norm(label)
        labels = [
            el
            for el in _all_descendants(self.elements)
            if el.tag == "label" and (_norm(el.text) == norm if exact else norm in _norm(el.text))
        ]
        for label_el in labels:
            target = label_el.get_attribute("for")
            if target:
                found = [el for el in self.page.root._all() if el._attrs.get("id") == target]
                if found:
                    return FakeLocator(found, self.page)
            controls = [
                el for el in label_el._descendants() if el.tag in ("input", "textarea", "select")
            ]
            if controls:
                return FakeLocator(controls, self.page)
        return FakeLocator([], self.page)

    def get_by_placeholder(self, text, exact=False):
        def _hit(el):
            value = el._attrs.get("placeholder") or ""
            return value == text if exact else text in value

        return FakeLocator(
            [el for el in _all_descendants(self.elements) if _hit(el)],
            self.page,
        )

    def get_by_test_id(self, testid):
        return FakeLocator(
            [
                el
                for el in _all_descendants(self.elements)
                if el._attrs.get("data-testid") == testid
            ],
            self.page,
        )


def _all_descendants(els):
    out = []
    for el in els:
        out.extend(el._descendants())
    return out


def _accessible_name(el) -> str:
    return _norm(el.get_attribute("aria-label") or el.text)


class FakePage:
    """A Playwright page over the same in-memory DOM as FakeEl."""

    def __init__(self, root, created_downloads=None):
        self.root = root
        self.visited: list[str] = []
        self.created_downloads = created_downloads if created_downloads is not None else []
        self.default_timeout_ms = None
        self.waited_ms: list[int] = []

    def goto(self, url):
        self.visited.append(url)

    def set_default_timeout(self, ms):
        self.default_timeout_ms = ms

    def wait_for_timeout(self, ms):
        self.waited_ms.append(ms)

    def locator(self, selector):
        return FakeLocator(_scope_find([self.root], selector), self)

    def get_by_text(self, text, exact=False):
        return FakeLocator([self.root], self).get_by_text(text, exact=exact)

    def get_by_role(self, role, name=None, exact=False):
        return FakeLocator([self.root], self).get_by_role(role, name=name, exact=exact)

    def get_by_label(self, label, exact=False):
        return FakeLocator([self.root], self).get_by_label(label, exact=exact)

    def get_by_placeholder(self, text, exact=False):
        return FakeLocator([self.root], self).get_by_placeholder(text, exact=exact)

    def get_by_test_id(self, testid):
        return FakeLocator([self.root], self).get_by_test_id(testid)

    def expect_download(self, timeout=None):
        return _FakeExpectDownload(self)


def _playwright_portal_dom(created: list) -> FakeEl:
    link = FakeEl(
        "a",
        text="Download report",
        attrs={"class": "download", "href": "/reports/daily.csv"},
    )
    link.on_click = lambda: created.append(FakeDownload("report.csv", "x,1\n"))
    return FakeEl(
        "html",
        children=[
            FakeEl(
                "body",
                children=[
                    FakeEl("input", attrs={"id": "username"}),
                    FakeEl("input", attrs={"id": "password"}),
                    FakeEl("button", text="Sign in", attrs={"type": "submit"}),
                    link,
                ],
            )
        ],
    )


def test_playwright_action_flow(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PORTAL_PASSWORD", "s3cret")
    staging = tmp_path / "staging"
    created: list = []
    page = FakePage(_playwright_portal_dom(created), created_downloads=created)
    source = PlaywrightSource(
        parse_source(
            {
                "type": "playwright",
                "actions": [
                    {"action": "goto", "url": "https://portal.example.com/login"},
                    {"action": "fill", "selector": "#username", "value": "alice"},
                    {"action": "fill", "selector": "#password", "secret_env": "PORTAL_PASSWORD"},
                    {"action": "click", "selector": "button[type=submit]"},
                    {"action": "wait_for", "selector": "a.download", "timeout_seconds": 5},
                    {"action": "download", "selector": "a.download", "timeout_seconds": 5},
                ],
            }
        ),
        page=page,
    )
    staged = source.download(staging, "run-1")
    assert [p.name for p in staged] == ["report.csv"]
    assert staged[0].read_text() == "x,1\n"
    assert page.visited == ["https://portal.example.com/login"]
    username = page.root.find_element("css selector", "#username")
    assert username.sent == ["alice"]
    password = page.root.find_element("css selector", "#password")
    assert password.sent == ["s3cret"]


def test_playwright_validations():
    with pytest.raises(PlaywrightSourceError, match="non-empty 'actions' list"):
        PlaywrightSource(PlaywrightSourceConfig(actions=()))
    with pytest.raises(PlaywrightSourceError, match="needs a 'download' action"):
        PlaywrightSource(PlaywrightSourceConfig(actions=({"action": "goto", "url": "https://x"},)))
    with pytest.raises(PlaywrightSourceError, match="unknown action"):
        PlaywrightSource(
            PlaywrightSourceConfig(
                actions=(
                    {"action": "teleport", "url": "https://x"},
                    {"action": "download", "selector": "a"},
                )
            )
        )
    with pytest.raises(PlaywrightSourceError, match="unknown selector kind"):
        PlaywrightSource(
            PlaywrightSourceConfig(
                actions=({"action": "download", "selector": {"kind": "aura", "aura": "x"}},)
            )
        )


def test_playwright_unset_secret_env(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("PORTAL_PASSWORD", raising=False)
    created: list = []
    page = FakePage(_playwright_portal_dom(created), created_downloads=created)
    source = PlaywrightSource(
        parse_source(
            {
                "type": "playwright",
                "actions": [
                    {"action": "fill", "selector": "#password", "secret_env": "PORTAL_PASSWORD"},
                    {"action": "download", "selector": "a.download"},
                ],
            }
        ),
        page=page,
    )
    with pytest.raises(PlaywrightSourceError, match="env var 'PORTAL_PASSWORD' is not set"):
        source.download(tmp_path / "staging", "run-1")


def test_playwright_download_timeout(tmp_path: Path):
    link = FakeEl("a", text="Download", attrs={"class": "download"})
    page = FakePage(FakeEl("html", children=[FakeEl("body", children=[link])]))
    source = PlaywrightSource(
        parse_source(
            {
                "type": "playwright",
                "actions": [{"action": "download", "selector": "a.download", "timeout_seconds": 1}],
            }
        ),
        page=page,
    )
    with pytest.raises(PlaywrightSourceError, match="download timed out"):
        source.download(tmp_path / "staging", "run-1")


def test_playwright_selectors():
    root = _table_dom()
    page = FakePage(root)
    link = resolve_selector_playwright(
        page,
        {
            "kind": "row",
            "table": "#files",
            "column": "Title",
            "match": "Q3 Report",
            "target": "a.download",
        },
    )
    assert link.elements[0].tag == "a" and link.elements[0].text == "Download"
    # chain: same row match, then the link inside it
    chained = resolve_selector_playwright(
        page,
        {
            "kind": "chain",
            "steps": [
                {"kind": "row", "table": "#files", "column": 0, "match": {"contains": "Q4"}},
                {"css": "a.download"},
            ],
        },
    )
    assert chained.elements[0].tag == "a"
    assert (
        resolve_selector_playwright(page, {"kind": "text", "text": "Export"}).elements[0].tag
        == "button"
    )
    assert (
        resolve_selector_playwright(page, {"kind": "testid", "testid": "export-btn"})
        .elements[0]
        .tag
        == "button"
    )
    assert (
        resolve_selector_playwright(page, {"kind": "placeholder", "placeholder": "Search"})
        .elements[0]
        .tag
        == "input"
    )
    with pytest.raises(PlaywrightSourceError, match="no row matched"):
        resolve_selector_playwright(
            page,
            {"kind": "row", "table": "#files", "column": "Title", "match": "Q9 Report"},
        )


class _PortalHandler(BaseHTTPRequestHandler):
    """Serves a portal page with one download link, for the real-browser test."""

    def do_GET(self):  # http.server naming
        if self.path == "/":
            body = (
                b'<html><body><a class="download" href="/report.csv">Download report</a>'
                b"</body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/report.csv":
            body = b"a,1\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Disposition", 'attachment; filename="report.csv"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


def test_playwright_real_browser_end_to_end(tmp_path: Path):
    """The real path: launch Chromium, click the link, expect_download
    hands the file over, the run seals a manifest and uploads it."""
    server = HTTPServer(("127.0.0.1", 0), _PortalHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        cfg = {
            "job": "portal",
            "source": {
                "type": "playwright",
                "actions": [
                    {"action": "goto", "url": f"http://127.0.0.1:{server.server_address[1]}/"},
                    {"action": "download", "selector": "a.download"},
                ],
            },
            "expect_csv": True,
            "rename_pattern": "{date}_{name}{ext}",
            "staging_dir": str(tmp_path / "staging"),
            "ledger_db": str(tmp_path / "agent.db"),
            "destination": {"transport": "local", "root": str(tmp_path / "dest")},
        }
        assert _run_once(cfg)["status"] == "UPLOADED"
        incoming = tmp_path / "dest" / "incoming" / "portal"
        assert len(list(incoming.glob("*.csv"))) == 1
        assert len(list(incoming.glob("*.manifest.json"))) == 1
    finally:
        server.shutdown()


def test_remote_files_optional_missing_skipped(tmp_path: Path):
    remote = tmp_path / "remote"
    inbox = remote / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "req.csv").write_text("a,1\n", encoding="utf-8")
    os.utime(inbox / "req.csv", (time.time() - 3600, time.time() - 3600))
    source = RemoteFilesSource(
        parse_source(
            {
                "type": "remote-files",
                "transport": "local",
                "root": str(remote),
                "min_age_seconds": 5,
                "files": [
                    {"directory": "inbox", "name": "req.csv"},
                    {"directory": "inbox", "name": "opt.csv", "optional": True},
                ],
            }
        ),
        "ftp-inbox",
    )
    staged = source.download(tmp_path / "staging", "run-1")
    assert [p.name for p in staged] == ["req.csv"]


def test_remote_files_optional_not_ready_skipped(tmp_path: Path):
    remote = tmp_path / "remote"
    inbox = remote / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "req.csv").write_text("a,1\n", encoding="utf-8")
    (inbox / "empty.csv").write_text("", encoding="utf-8")
    (inbox / "young.csv").write_text("y,1\n", encoding="utf-8")  # mtime is now
    for name in ("req.csv", "empty.csv"):
        os.utime(inbox / name, (time.time() - 3600, time.time() - 3600))
    source = RemoteFilesSource(
        parse_source(
            {
                "type": "remote-files",
                "transport": "local",
                "root": str(remote),
                "min_age_seconds": 300,
                "files": [
                    {"directory": "inbox", "name": "req.csv"},
                    {"directory": "inbox", "name": "empty.csv", "optional": True},
                    {"directory": "inbox", "name": "young.csv", "optional": True},
                ],
            }
        ),
        "ftp-inbox",
    )
    staged = source.download(tmp_path / "staging", "run-1")
    assert [p.name for p in staged] == ["req.csv"]


def test_remote_files_all_optional_missing_skips_run(tmp_path: Path):
    (tmp_path / "remote" / "inbox").mkdir(parents=True)
    cfg = {
        "job": "ftp-inbox",
        "source": {
            "type": "remote-files",
            "transport": "local",
            "root": str(tmp_path / "remote"),
            "min_age_seconds": 5,
            "files": [{"directory": "inbox", "name": "opt.csv", "optional": True}],
        },
        "staging_dir": str(tmp_path / "staging"),
        "ledger_db": str(tmp_path / "agent.db"),
        "destination": {"transport": "local", "root": str(tmp_path / "dest")},
    }
    assert _run_once(cfg)["status"] == "SKIPPED"
    assert not list((tmp_path / "dest").rglob("*.manifest.json"))
    assert not list((tmp_path / "staging").iterdir())  # run staging dir removed


def test_remote_files_required_missing_still_fails_loud(tmp_path: Path):
    remote = tmp_path / "remote"
    inbox = remote / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "opt.csv").write_text("o,1\n", encoding="utf-8")
    os.utime(inbox / "opt.csv", (time.time() - 3600, time.time() - 3600))
    source = RemoteFilesSource(
        parse_source(
            {
                "type": "remote-files",
                "transport": "local",
                "root": str(remote),
                "min_age_seconds": 5,
                "files": [
                    {"directory": "inbox", "name": "req.csv"},
                    {"directory": "inbox", "name": "opt.csv", "optional": True},
                ],
            }
        ),
        "ftp-inbox",
    )
    with pytest.raises(ValueError, match="file not found"):
        source.download(tmp_path / "staging", "run-1")


def test_local_dir_empty_skips_run(tmp_path: Path):
    drop = tmp_path / "drop"
    drop.mkdir()
    cfg = {
        "job": "drop",
        "source": {"type": "local-dir", "directory": str(drop)},
        "staging_dir": str(tmp_path / "staging"),
        "ledger_db": str(tmp_path / "agent.db"),
        "destination": {"transport": "local", "root": str(tmp_path / "dest")},
    }
    assert _run_once(cfg)["status"] == "SKIPPED"
