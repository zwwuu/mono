"""Infrastructure -- SMB source: fetch files from a Windows share.

The same drop-folder idea as DirectorySource, over SMB via
``smbprotocol`` (``pip install smbprotocol`` -- an optional
dependency, like paramiko is for SFTP): list the share directory,
keep names matching ``pattern``, skip .part files, files still
being written (mtime younger than ``min_age_seconds``), and empty
files. One run takes every matching file as one batch.

The password comes from the env var named in ``password_env``
(default FILEFLOW_SMB_PASSWORD) -- never from the config file.
The connection is per download() call: opened, used, closed.
``client`` is injectable for tests: any object with listdir/stat/
open_file shaped like the ``smbclient`` module functions.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import time
from pathlib import Path

from .filesystem import PART_SUFFIX, log


class SmbSource:
    def __init__(self, cfg: dict, client=None):
        self.server = cfg["server"]
        self.share = cfg["share"]
        self.path = cfg.get("path", "")
        self.pattern = cfg.get("pattern", "*")
        self.min_age = cfg.get("min_age_seconds", 5)
        self.username = cfg.get("username", "")
        self.password = os.environ.get(cfg.get("password_env", "FILEFLOW_SMB_PASSWORD"), "")
        if client is None:
            try:
                import smbclient
            except ImportError as e:
                raise ImportError("smb source needs smbprotocol: pip install smbprotocol") from e
            client = smbclient
        self._client = client

    def _unc(self, name: str = "") -> str:
        parts = [f"\\\\{self.server}\\{self.share}"]
        if self.path:
            parts.append(self.path.replace("/", "\\"))
        if name:
            parts.append(name)
        return "\\".join(parts)

    def _auth(self) -> dict:
        return {"username": self.username, "password": self.password}

    def download(self, dest_dir: Path, run_id: str) -> list[Path]:
        dest_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        staged: list[Path] = []
        for name in sorted(self._client.listdir(self._unc(), **self._auth())):
            if not fnmatch.fnmatch(name, self.pattern) or name.endswith(PART_SUFFIX):
                continue
            try:
                info = self._client.stat(self._unc(name), **self._auth())
            except OSError as e:
                log.warning("smb source: cannot stat %s: %s", name, e)
                continue
            if now - info.st_mtime < self.min_age:
                continue  # still being written; try next run
            if info.st_size == 0:
                log.warning("smb source %s: empty file, skipped", name)
                continue
            final = dest_dir / name
            tmp = dest_dir / (name + PART_SUFFIX)
            with self._client.open_file(self._unc(name), mode="rb", **self._auth()) as remote:
                with open(tmp, "wb") as f:
                    shutil.copyfileobj(remote, f)
            tmp.replace(final)
            staged.append(final)
        return staged
