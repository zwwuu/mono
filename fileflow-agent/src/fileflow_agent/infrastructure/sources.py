"""Infrastructure -- sources: where the agent fetches files from.

Each source implements the domain's Source port: download into a
staging directory (via .part + rename, never a partial file) and
return the completed local paths. One run fetches one or many files,
but it is always one manifest. A new source type is a new class
here plus one line in build_source -- no use-case change.

Remote sources (remote-dir, remote-files, smb, selenium) open their
connection inside download() and close it before returning, so a
--loop pass never leaks a connection. Tests can inject the backend
instead: RemoteDirectorySource / RemoteFilesSource take an optional
transport, SmbSource an optional smbclient-like object,
SeleniumSource an optional driver.
"""

from __future__ import annotations

import fnmatch
import shutil
import time
import urllib.request
from pathlib import Path

from .. import __version__
from ..domain.integrity import Context
from ..domain.ports import Source
from .config import (
    LocalDirSourceConfig,
    LocalSourceConfig,
    RemoteDirSourceConfig,
    RemoteFilesSourceConfig,
    SourceConfig,
    WebsiteSourceConfig,
)
from .filesystem import PART_SUFFIX, log
from .transport import LocalTransport, Transport, build_transport


class WebsiteSource:
    """HTTP(S) download. Plain urllib on purpose: no extra dependency
    for a file fetch, and the agent never needs a browser here."""

    def __init__(self, cfg: WebsiteSourceConfig):
        self.url = cfg.url
        self.timeout = cfg.timeout_seconds

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

    def __init__(self, cfg: LocalSourceConfig):
        self.path = Path(cfg.path)

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

    def __init__(self, cfg: LocalDirSourceConfig):
        self.directory = Path(cfg.directory)
        self.pattern = cfg.pattern
        self.min_age = cfg.min_age_seconds

    def download(self, dest_dir: Path, run_id: str) -> list[Path]:
        dest_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        staged: list[Path] = []
        for src in sorted(self.directory.glob(self.pattern)):
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


def build_source(
    source: SourceConfig,
    variables: dict | None = None,
) -> Source:
    """Build a source from a parsed ``source`` section. ``variables``
    is the job's template context (remote-files templates render
    against it); sources that do not template anything ignore it.
    The config parser has already validated the section, so an
    unknown type cannot reach this function."""
    if source.type == "website":
        return WebsiteSource(source)
    if source.type == "local":
        return LocalSource(source)
    if source.type == "local-dir":
        return DirectorySource(source)
    if source.type == "remote-dir":
        return RemoteDirectorySource(source)
    if source.type == "remote-files":
        return RemoteFilesSource(source, variables)
    if source.type == "smb":
        from .smb_source import SmbSource

        return SmbSource(source)
    if source.type == "selenium":
        from .selenium_source import SeleniumSource

        return SeleniumSource(source)
    if source.type == "playwright":
        from .playwright_source import PlaywrightSource

        return PlaywrightSource(source)
    raise ValueError(f"unknown source type {source.type!r}")


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

    def __init__(self, cfg: RemoteDirSourceConfig, transport: Transport | None = None):
        self.cfg = cfg
        self.remote_dir = cfg.remote_dir
        self.pattern = cfg.pattern
        self.min_age = cfg.min_age_seconds
        self._transport = transport
        self._owns_transport = transport is None

    def _open_transport(self) -> Transport:
        if self._transport is not None:
            return self._transport
        if self.cfg.transport.kind == "local":
            return LocalTransport(self.cfg.transport.root)
        return build_transport(self.cfg.transport)

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


class RemoteFilesSource:
    """Fetch named files from a remote server (FTP/SFTP).

    ``files`` is a list of {"directory", "name"} templates, rendered
    per run against the job's ``variables`` -- there are no built-in
    tokens, so a template can only use what the job declares. All of
    them are one batch under one manifest. Every listed file is
    required by default: a missing, empty, or still-being-written
    file fails the run loud instead of uploading a partial batch --
    a named file was explicitly asked for, so its absence is an
    error, not "no files". An entry with ``"optional": true`` is the
    exception: when it is missing, empty, or still being written it
    is logged and skipped, and the batch ships without it. If every
    entry is optional and none is ready, the source returns no files
    and the run is SKIPPED (no manifest, no upload).

    Connection comes from the source config; the password resolves
    the same way as the upload transport (env var first, literal
    ``password`` as fallback). ``transport: "local"`` (with ``root``)
    is the pilot/test mode. The transport is opened here and closed
    before download() returns.
    """

    def __init__(
        self,
        cfg: RemoteFilesSourceConfig,
        variables: dict | None = None,
        transport: Transport | None = None,
    ):
        self.cfg = cfg
        self.variables = variables or {}
        self.files = cfg.files
        if not self.files:
            raise ValueError("remote-files source needs a non-empty 'files' list")
        self.min_age = cfg.min_age_seconds
        self._transport = transport
        self._owns_transport = transport is None

    def _open_transport(self) -> Transport:
        if self._transport is not None:
            return self._transport
        if self.cfg.transport.kind == "local":
            return LocalTransport(self.cfg.transport.root)
        return build_transport(self.cfg.transport)

    def download(self, dest_dir: Path, run_id: str) -> list[Path]:
        dest_dir.mkdir(parents=True, exist_ok=True)
        # One Context per download, from the job's variables only.
        ctx = Context(self.variables)
        transport = self._open_transport()
        try:
            staged: list[Path] = []
            for entry in self.files:
                remote_dir = ctx.remote_path(entry.directory)
                name = ctx.render(entry.name)
                if "/" in name or "\\" in name:
                    raise ValueError(f"remote-files: name template produced a path: {name!r}")
                where = f"{remote_dir}/{name}"
                size = transport.remote_size(remote_dir, name)
                if size is None:
                    if entry.optional:
                        log.info("remote-files: optional file not present, skipped: %s", where)
                        continue
                    raise ValueError(f"remote-files: file not found: {where}")
                if size == 0:
                    if entry.optional:
                        log.info("remote-files: optional file is empty, skipped: %s", where)
                        continue
                    raise ValueError(f"remote-files: file is empty: {where}")
                mtime = transport.remote_mtime(remote_dir, name)
                if mtime is not None and time.time() - mtime < self.min_age:
                    if entry.optional:
                        log.info(
                            "remote-files: optional file still being written, skipped: %s", where
                        )
                        continue
                    raise ValueError(f"remote-files: file still being written: {where}")
                transport.download(remote_dir, name, dest_dir / name)
                staged.append(dest_dir / name)
            return staged
        finally:
            if self._owns_transport:
                transport.close()
