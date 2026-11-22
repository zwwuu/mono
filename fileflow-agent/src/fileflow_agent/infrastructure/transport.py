"""Transports used by the PC agent to upload to the FTP server.

Only the agent needs a transport: the watcher runs *on* the FTP server,
so it reads the FTP directories as local paths and publishes to the
share drive through its local SMB mount.

Every upload follows the same contract, whatever the transport:
  1. upload the data file as ``<name>.part``
  2. rename ``<name>.part`` -> ``<name>``  (atomic on FTP/SFTP/local)
  3. upload the manifest the same way, *last* -- its rename is the
     trigger that tells the watcher the transfer is complete.
  4. verify size (and, for SFTP/local, hash) of what landed.

Transports:
  * ``local`` -- plain filesystem copy. For tests / pilot only, where
    the "FTP server" is a shared folder. No network involved.
  * ``ftp``   -- plain FTP (ftplib, stdlib). Internal LAN only.
  * ``sftp``  -- SFTP via paramiko. Preferred on a real network:
    encrypted credentials + data. ``pip install paramiko``.
"""

from __future__ import annotations

import ftplib
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .filesystem import PART_SUFFIX, log


class Transport:
    kind = "base"

    def upload(self, local_path: str | Path, remote_dir: str, remote_name: str) -> None:
        raise NotImplementedError

    def download(self, remote_dir: str, remote_name: str, local_path: str | Path) -> None:
        """Download a remote file to local_path (.part + rename locally)."""
        raise NotImplementedError

    def list_names(self, remote_dir: str) -> list[str]:
        """Names (not paths) present in remote_dir; [] if it does not exist."""
        raise NotImplementedError

    def delete(self, remote_dir: str, remote_name: str) -> None:
        raise NotImplementedError

    def remote_size(self, remote_dir: str, remote_name: str) -> int | None:
        raise NotImplementedError

    def remote_mtime(self, remote_dir: str, remote_name: str) -> float | None:
        """mtime of a remote file as epoch seconds. The default is
        None (unknown): a transport that cannot report mtimes leaves
        the source to treat every file as stable."""
        return None

    def close(self) -> None:
        pass

    # -- shared helpers -------------------------------------------------
    def upload_many_with_manifest(
        self,
        files: list[tuple[str | Path, str]],
        manifest_path: str | Path,
        remote_dir: str,
    ) -> None:
        """Upload every data file first, manifest last (its rename is
        the trigger that tells the watcher the batch is complete)."""
        for local_path, remote_name in files:
            self.upload(local_path, remote_dir, remote_name)
            self._verify_size(local_path, remote_dir, remote_name)
        manifest_name = Path(manifest_path).name
        self.upload(manifest_path, remote_dir, manifest_name)
        self._verify_size(manifest_path, remote_dir, manifest_name)
        log.info("uploaded %d file(s) + %s to %s", len(files), manifest_name, remote_dir)

    def _verify_size(self, local_path: str | Path, remote_dir: str, remote_name: str) -> None:
        expected = Path(local_path).stat().st_size
        actual = self.remote_size(remote_dir, remote_name)
        if actual is None:
            # The transport could not stat the remote file (e.g. an FTP
            # server without SIZE support). That is not proof it landed,
            # so say so loudly instead of passing silently.
            log.warning("could not verify size of %s after upload", remote_name)
            return
        if actual != expected:
            raise OSError(
                f"size mismatch after upload: {remote_name} "
                f"expected {expected}, remote has {actual}"
            )


def _download_local(local_path: str | Path, write_tmp) -> Path:
    """Write a downloaded file via .part + rename; never a partial file.

    ``write_tmp`` fills the .part file; the rename is what publishes it.
    """
    local_path = Path(local_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = local_path.with_name(local_path.name + PART_SUFFIX)
    write_tmp(tmp)
    tmp.replace(local_path)
    return local_path


class LocalTransport(Transport):
    """Filesystem transport: remote_dir is relative to ``root``."""

    kind = "local"

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _remote(self, remote_dir: str, remote_name: str) -> Path:
        return self.root / remote_dir / remote_name

    def upload(self, local_path: str | Path, remote_dir: str, remote_name: str) -> None:
        dst = self._remote(remote_dir, remote_name)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + PART_SUFFIX)
        shutil.copyfile(local_path, tmp)
        tmp.replace(dst)

    def download(self, remote_dir: str, remote_name: str, local_path: str | Path) -> None:
        src = self._remote(remote_dir, remote_name)
        _download_local(local_path, lambda tmp: shutil.copyfile(src, tmp))

    def list_names(self, remote_dir: str) -> list[str]:
        d = self.root / remote_dir
        return sorted(p.name for p in d.iterdir()) if d.is_dir() else []

    def delete(self, remote_dir: str, remote_name: str) -> None:
        self._remote(remote_dir, remote_name).unlink(missing_ok=True)

    def remote_size(self, remote_dir: str, remote_name: str) -> int | None:
        p = self._remote(remote_dir, remote_name)
        return p.stat().st_size if p.exists() else None

    def remote_mtime(self, remote_dir: str, remote_name: str) -> float | None:
        p = self._remote(remote_dir, remote_name)
        return p.stat().st_mtime if p.exists() else None


class FtpTransport(Transport):
    kind = "ftp"

    def __init__(
        self, host: str, port: int = 21, username: str = "", password: str = "", timeout: int = 30
    ):
        self.ftp = ftplib.FTP()
        self.ftp.connect(host, port, timeout=timeout)
        self.ftp.login(username, password)

    def upload(self, local_path: str | Path, remote_dir: str, remote_name: str) -> None:
        self.ftp.cwd(remote_dir)
        part = remote_name + PART_SUFFIX
        with open(local_path, "rb") as f:
            self.ftp.storbinary(f"STOR {part}", f)
        self.ftp.rename(part, remote_name)

    def download(self, remote_dir: str, remote_name: str, local_path: str | Path) -> None:
        self.ftp.cwd(remote_dir)

        def _retr(tmp: Path) -> None:
            with open(tmp, "wb") as f:
                self.ftp.retrbinary(f"RETR {remote_name}", f.write)

        _download_local(local_path, _retr)

    def list_names(self, remote_dir: str) -> list[str]:
        try:
            self.ftp.cwd(remote_dir)
            return sorted(self.ftp.nlst())
        except (ftplib.error_perm, OSError) as _:
            return []

    def delete(self, remote_dir: str, remote_name: str) -> None:
        try:
            self.ftp.cwd(remote_dir)
            self.ftp.delete(remote_name)
        except (ftplib.error_perm, OSError) as _:
            pass

    def remote_size(self, remote_dir: str, remote_name: str) -> int | None:
        try:
            self.ftp.cwd(remote_dir)
            return self.ftp.size(remote_name)
        except (ftplib.error_perm, OSError) as _:
            return None

    def remote_mtime(self, remote_dir: str, remote_name: str) -> float | None:
        """MDTM, the standard FTP mtime command (UTC, YYYYMMDDHHMMSS).
        None when the server does not support it."""
        try:
            self.ftp.cwd(remote_dir)
            resp = self.ftp.sendcmd(f"MDTM {remote_name}")
        except (ftplib.error_perm, OSError) as _:
            return None
        try:
            stamp = resp.split(None, 1)[1].strip()
            return datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).timestamp()
        except (IndexError, ValueError) as _:
            return None

    def close(self) -> None:
        try:
            self.ftp.quit()
        except (ftplib.Error, OSError) as _:
            pass


class SftpTransport(Transport):
    kind = "sftp"

    def __init__(
        self,
        host: str,
        port: int = 22,
        username: str = "",
        password: str | None = None,
        key_file: str | None = None,
        timeout: int = 30,
    ):
        try:
            import paramiko
        except ImportError as e:
            raise ImportError("sftp transport needs paramiko: pip install paramiko") from e
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            host,
            port=port,
            username=username,
            password=password,
            key_filename=key_file,
            timeout=timeout,
            allow_agent=False,
            look_for_keys=False,
        )
        self._client = client
        self.sftp = client.open_sftp()

    def upload(self, local_path: str | Path, remote_dir: str, remote_name: str) -> None:
        part = f"{remote_dir}/{remote_name}{PART_SUFFIX}"
        final = f"{remote_dir}/{remote_name}"
        self.sftp.put(str(local_path), part)
        self.sftp.rename(part, final)

    def download(self, remote_dir: str, remote_name: str, local_path: str | Path) -> None:
        _download_local(
            local_path, lambda tmp: self.sftp.get(f"{remote_dir}/{remote_name}", str(tmp))
        )

    def list_names(self, remote_dir: str) -> list[str]:
        try:
            return sorted(self.sftp.listdir(remote_dir))
        except OSError:
            return []

    def delete(self, remote_dir: str, remote_name: str) -> None:
        try:
            self.sftp.remove(f"{remote_dir}/{remote_name}")
        except OSError:
            pass

    def remote_size(self, remote_dir: str, remote_name: str) -> int | None:
        try:
            return self.sftp.stat(f"{remote_dir}/{remote_name}").st_size
        except OSError:
            return None

    def remote_mtime(self, remote_dir: str, remote_name: str) -> float | None:
        try:
            return self.sftp.stat(f"{remote_dir}/{remote_name}").st_mtime
        except OSError:
            return None

    def close(self) -> None:
        try:
            self.sftp.close()
            self._client.close()
        except (OSError, Exception) as e:  # noqa: BLE001 -- close() must never raise
            log.debug("sftp close failed (ignored): %s", e)


def build_transport(cfg: dict) -> Transport:
    """Build a transport from the agent config ``ftp`` section.

    {"transport": "sftp", "host": ..., "port": 22, "username": ...}
    Password / key come from env vars, never from the config file:
    FILEFLOW_FTP_PASSWORD / FILEFLOW_SFTP_KEY_FILE.
    """
    kind = cfg.get("transport", "local")
    if kind == "local":
        return LocalTransport(cfg["root"])
    if kind == "ftp":
        return FtpTransport(
            cfg["host"],
            cfg.get("port", 21),
            cfg.get("username", ""),
            os.environ.get("FILEFLOW_FTP_PASSWORD", ""),
        )
    if kind == "sftp":
        return SftpTransport(
            cfg["host"],
            cfg.get("port", 22),
            cfg.get("username", ""),
            os.environ.get("FILEFLOW_FTP_PASSWORD") or None,
            os.environ.get("FILEFLOW_SFTP_KEY_FILE"),
        )
    raise ValueError(f"unknown transport {kind!r} (want local|ftp|sftp)")
