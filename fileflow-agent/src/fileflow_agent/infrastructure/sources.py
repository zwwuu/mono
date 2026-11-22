"""Infrastructure -- sources: where the agent fetches files from.

Each source implements the domain's Source port: download into a
staging directory (via .part + rename, never a partial file) and
return the completed local paths. One run fetches one or many files,
but it is always one manifest. A new source type is a new class
here plus one line in build_source -- no use-case change.

Remote sources (remote-dir, smb, selenium) open their connection
inside download() and close it before returning, so a --loop pass
never leaks a connection. Tests can inject the backend instead:
RemoteDirectorySource takes an optional transport, SmbSource an
optional smbclient-like object, SeleniumSource an optional driver.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import time
import urllib.request
from pathlib import Path

from .. import __version__
from ..domain.ports import Source
from .filesystem import PART_SUFFIX, log
from .transport import FtpTransport, LocalTransport, SftpTransport, Transport


class WebsiteSource:
    """HTTP(S) download. Plain urllib on purpose: no extra dependency
    for a file fetch, and the agent never needs a browser here."""

    def __init__(self, cfg: dict):
        self.url = cfg["url"]
        self.timeout = cfg.get("timeout_seconds", 60)

    def download(self, dest_dir: Path, run_id: str) -> list[Path]:
        dest_dir.mkdir(parents=True, exist_ok=True)
        name = Path(urllib.request.urlparse(self.url).path).name or "download"
        final = dest_dir / name
        tmp = dest_dir / (name + PART_SUFFIX)
        req = urllib.request.Request(
            self.url, headers={"User-Agent": f"FileFlow-Agent/{__version__}"}
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        tmp.replace(final)
        return [final]


class LocalSource:
    """A file already on disk (a drop-folder, or tests)."""

    def __init__(self, cfg: dict):
        self.path = Path(cfg["path"])

    def download(self, dest_dir: Path, run_id: str) -> list[Path]:
        dest_dir.mkdir(parents=True, exist_ok=True)
        final = dest_dir / self.path.name
        tmp = dest_dir / (self.path.name + PART_SUFFIX)
        shutil.copyfile(self.path, tmp)
        tmp.replace(final)
        return [final]


class DirectorySource:
    """Every stable file in a local directory (a drop folder).

    Files are picked by glob ``pattern`` (default "*"), sorted by
    name, skipping .part files, files still being written (mtime
    younger than ``min_age_seconds``, default 2s), and empty files.
    One run takes every matching file as one batch -- the same idea
    as the watcher's outbound batch, in the forward direction.
    """

    def __init__(self, cfg: dict):
        self.dir = Path(cfg["dir"])
        self.pattern = cfg.get("pattern", "*")
        self.min_age = cfg.get("min_age_seconds", 2)

    def download(self, dest_dir: Path, run_id: str) -> list[Path]:
        dest_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        staged: list[Path] = []
        for src in sorted(self.dir.glob(self.pattern)):
            if not src.is_file() or src.suffix == PART_SUFFIX:
                continue
            if now - src.stat().st_mtime < self.min_age:
                continue  # still being written; try next run
            if src.stat().st_size == 0:
                log.warning("source %s: empty file, skipped", src.name)
                continue
            final = dest_dir / src.name
            tmp = dest_dir / (src.name + PART_SUFFIX)
            shutil.copyfile(src, tmp)
            tmp.replace(final)
            staged.append(final)
        return staged


def build_source(cfg: dict) -> Source:
    kind = cfg["type"]
    if kind == "website":
        return WebsiteSource(cfg)
    if kind == "local":
        return LocalSource(cfg)
    if kind == "local-dir":
        return DirectorySource(cfg)
    if kind == "remote-dir":
        return RemoteDirectorySource(cfg)
    if kind == "smb":
        from .smb_source import SmbSource

        return SmbSource(cfg)
    if kind == "selenium":
        from .selenium_source import SeleniumSource

        return SeleniumSource(cfg)
    raise ValueError(
        f"unknown source type {kind!r} (want website|local|local-dir|remote-dir|smb|selenium)"
    )


class RemoteDirectorySource:
    """Every stable file in a remote directory (an FTP/SFTP inbox).

    The same drop-folder idea as DirectorySource, over a transport:
    list ``remote_dir``, keep names matching ``pattern`` (default "*"),
    skip .part files, files still being written (mtime younger than
    ``min_age_seconds``, default 5s -- remote clocks can lag, so the
    default is longer than local-dir's 2s), and empty files. One run
    takes every matching file as one batch. The transport is opened
    here and closed before download() returns.

    Connection comes from the source config; the password comes from
    the env var named in ``password_env`` (default
    FILEFLOW_FTP_PASSWORD), the SFTP key file from ``key_file_env``
    (default FILEFLOW_SFTP_KEY_FILE) -- never from the config file.
    ``transport: "local"`` (with ``root``) is the pilot/test mode:
    the "remote" directory is a local folder.
    """

    def __init__(self, cfg: dict, transport: Transport | None = None):
        self.cfg = cfg
        self.remote_dir = cfg["remote_dir"]
        self.pattern = cfg.get("pattern", "*")
        self.min_age = cfg.get("min_age_seconds", 5)
        self._transport = transport
        self._owns_transport = transport is None

    def _open_transport(self) -> Transport:
        if self._transport is not None:
            return self._transport
        kind = self.cfg.get("transport", "ftp")
        if kind == "local":
            return LocalTransport(self.cfg["root"])
        host = self.cfg["host"]
        port = self.cfg.get("port", 21 if kind == "ftp" else 22)
        username = self.cfg.get("username", "")
        password = os.environ.get(self.cfg.get("password_env", "FILEFLOW_FTP_PASSWORD"), "")
        if kind == "ftp":
            return FtpTransport(host, port, username, password)
        if kind == "sftp":
            key_file = os.environ.get(self.cfg.get("key_file_env", "FILEFLOW_SFTP_KEY_FILE"))
            return SftpTransport(host, port, username, password or None, key_file)
        raise ValueError(f"unknown remote-dir transport {kind!r} (want local|ftp|sftp)")

    def download(self, dest_dir: Path, run_id: str) -> list[Path]:
        dest_dir.mkdir(parents=True, exist_ok=True)
        transport = self._open_transport()
        try:
            now = time.time()
            staged: list[Path] = []
            for name in sorted(transport.list_names(self.remote_dir)):
                if not fnmatch.fnmatch(name, self.pattern) or name.endswith(PART_SUFFIX):
                    continue
                mtime = transport.remote_mtime(self.remote_dir, name)
                if mtime is not None and now - mtime < self.min_age:
                    continue  # still being written; try next run
                size = transport.remote_size(self.remote_dir, name)
                if size == 0:
                    log.warning("source %s: empty file, skipped", name)
                    continue
                transport.download(self.remote_dir, name, dest_dir / name)
                staged.append(dest_dir / name)
            return staged
        finally:
            if self._owns_transport:
                transport.close()
