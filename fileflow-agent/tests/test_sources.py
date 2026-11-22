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
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fileflow_agent.adapters.cli import build_download_use_case
from fileflow_agent.infrastructure.selenium_source import (
    SeleniumSource,
    SeleniumSourceError,
    resolve_selector,
)
from fileflow_agent.infrastructure.smb_source import SmbSource
from fileflow_agent.infrastructure.sources import RemoteDirectorySource, build_source


def _run_once(cfg: dict) -> dict:
    use_case, transport, repository = build_download_use_case(cfg)
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
        {
            "type": "remote-dir",
            "transport": "local",
            "root": str(remote),
            "remote_dir": ".",
            "pattern": "*.csv",
            "min_age_seconds": 2,
        }
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
        "agent_procedure": "normalize-csv",
        "rename_pattern": "{date}_{name}{ext}",
        "staging_dir": str(tmp_path / "staging"),
        "ledger_db": str(tmp_path / "agent.db"),
        "ftp": {"transport": "local", "root": str(tmp_path / "ftp")},
    }
    assert _run_once(cfg)["status"] == "UPLOADED"
    assert _run_once(cfg)["status"] == "SKIPPED"  # second pass: already processed


def test_build_source_rejects_unknown_type():
    with pytest.raises(ValueError, match="unknown source type"):
        build_source({"type": "carrier-pigeon"})


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
        {
            "type": "smb",
            "server": "fileserver",
            "share": "reports",
            "path": "daily",
            "pattern": "*.csv",
            "min_age_seconds": 2,
        },
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
        {
            "type": "selenium",
            "actions": [
                {"action": "goto", "url": "https://portal.example.com/login"},
                {"action": "fill", "selector": "#username", "value": "alice"},
                {"action": "fill", "selector": "#password", "secret": "PORTAL_PASSWORD"},
                {"action": "click", "selector": "button[type=submit]"},
                {"action": "wait_for", "selector": "a.download", "timeout_seconds": 5},
                {"action": "download", "selector": "a.download", "timeout_seconds": 5},
            ],
        },
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
        SeleniumSource({"type": "selenium", "actions": []})
    with pytest.raises(SeleniumSourceError, match="needs a 'download' action"):
        SeleniumSource({"type": "selenium", "actions": [{"action": "goto", "url": "https://x"}]})
    with pytest.raises(SeleniumSourceError, match="unknown action"):
        SeleniumSource(
            {
                "type": "selenium",
                "actions": [
                    {"action": "teleport", "url": "https://x"},
                    {"action": "download", "selector": "a"},
                ],
            }
        )
    with pytest.raises(SeleniumSourceError, match="unknown selector kind"):
        SeleniumSource(
            {
                "type": "selenium",
                "actions": [{"action": "download", "selector": {"kind": "aura", "aura": "x"}}],
            }
        )


def test_selenium_unset_secret(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("PORTAL_PASSWORD", raising=False)
    driver = FakeDriver(_portal_dom(tmp_path / ".downloads"))
    source = SeleniumSource(
        {
            "type": "selenium",
            "actions": [
                {"action": "fill", "selector": "#password", "secret": "PORTAL_PASSWORD"},
                {"action": "download", "selector": "a.download"},
            ],
        },
        driver=driver,
    )
    with pytest.raises(SeleniumSourceError, match="env var 'PORTAL_PASSWORD' is not set"):
        source.download(tmp_path / "staging", "run-1")


def test_selenium_download_timeout(tmp_path: Path):
    link = FakeEl("a", text="Download", attrs={"class": "download"})
    driver = FakeDriver(FakeEl("html", children=[FakeEl("body", children=[link])]))
    source = SeleniumSource(
        {
            "type": "selenium",
            "actions": [{"action": "download", "selector": "a.download", "timeout_seconds": 1}],
        },
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
