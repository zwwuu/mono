"""Transports used by the watcher to talk to FTP 1 and FTP 2.

The watcher no longer reads FTP directories as local paths: both
servers are reached through a transport, so the two FTPs can be
separate servers or two folders on one box -- the config decides.
This module is a deliberate copy of the agent's transport design
(``fileflow_agent.infrastructure.transport``): the two projects
share no code by design -- small, stable, per-machine -- plus two
operations the watcher needs that the agent does not: ``move``
(archive a finished original on FTP 1) and ``makedirs`` (ensure
a remote directory exists before uploading into it).

Every upload follows the same contract, whatever the transport:
  1. upload the data file as ``<name>.part``
  2. rename ``<name>.part`` -> ``<name>``  (atomic on FTP/SFTP/local)
  3. upload the manifest the same way, *last* -- its rename is the
     trigger that tells the other side the transfer is complete.
  4. verify size, then SHA-256 (download-back), of what landed.

Multi-file batches upload concurrently (4 workers, each with its
own transport clone -- FTP/SFTP connections are not shareable
across threads); the manifest still goes last, alone.

Transports:
  * ``local`` -- plain filesystem copy. For tests / pilot only, where
    the "FTP server" is a shared folder. No network involved.
  * ``ftp``   -- plain FTP (ftplib, stdlib). Internal LAN only.
  * ``sftp``  -- SFTP via paramiko. Preferred on a real network:
    encrypted credentials + data. ``pip install fileflow-watcher[sftp]``.
"""

from __future__ import annotations

import ftplib
import os
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from fileflow_contract import sha256_file

from .config import TransportConfig
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
        the caller to treat every file as stable."""
        return None

    def move(self, remote_dir: str, remote_name: str, dest_dir: str, dest_name: str) -> None:
        """Move a file within one transport (rename). The destination
        directory is created first."""
        raise NotImplementedError

    def makedirs(self, remote_dir: str) -> None:
        """Ensure a remote directory exists, creating parents as needed."""
        raise NotImplementedError

    def close(self) -> None:
        pass

    def clone(self) -> Transport:
        """A fresh transport of the same kind, for a worker thread:
        FTP/SFTP connections are not shareable across threads, so
        each parallel upload gets its own connection."""
        raise NotImplementedError

    def upload_verified(self, local_path: str | Path, remote_dir: str, remote_name: str) -> None:
        """Upload one file and verify what landed: size first (fast
        fail), then SHA-256 by downloading it back -- FTP has no
        remote hash command, so download-back is the only way. A
        size mismatch raises OSError (a truncated upload may succeed
        on retry); a hash mismatch raises ValueError (verification
        failed deterministically -- retrying would fail the same way,
        so the retry helper does not retry it)."""
        self.upload(local_path, remote_dir, remote_name)
        self._verify_size(local_path, remote_dir, remote_name)
        self._verify_hash(local_path, remote_dir, remote_name)

    def upload_many(self, files: list[tuple[str | Path, str]], remote_dir: str) -> None:
        """Upload several files, each verified (size + SHA-256). More
        than one file goes up concurrently (4 workers, each with its
        own transport clone); one file uses this transport directly,
        exactly as before. A failure in any file raises (the first,
        in order) and the caller uploads no manifest."""
        if len(files) <= 1:
            for local_path, remote_name in files:
                self.upload_verified(local_path, remote_dir, remote_name)
            return
        clones: list[Transport] = []
        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = []
                for local_path, remote_name in files:
                    worker = self.clone()
                    clones.append(worker)
                    futures.append(
                        pool.submit(worker.upload_verified, local_path, remote_dir, remote_name)
                    )
                for fut in futures:
                    fut.result()
        finally:
            for worker in clones:
                worker.close()

    # -- shared helpers -------------------------------------------------
    def upload_many_with_manifest(
        self,
        files: list[tuple[str | Path, str]],
        manifest_path: str | Path,
        remote_dir: str,
    ) -> None:
        """Upload every data file first, manifest last (its rename is
        the trigger that tells the other side the batch is complete)."""
        self.upload_many(files, remote_dir)
        manifest_name = Path(manifest_path).name
        self.upload_verified(manifest_path, remote_dir, manifest_name)
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

    def _verify_hash(self, local_path: str | Path, remote_dir: str, remote_name: str) -> None:
        """Download the remote file back to a temp file and compare
        its SHA-256 against the local file's. Raises ValueError on a
        mismatch (see upload_verified for why it is not OSError)."""
        expected = sha256_file(local_path)
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / remote_name
            self.download(remote_dir, remote_name, probe)
            actual = sha256_file(probe)
        if actual != expected:
            raise ValueError(
                f"hash mismatch after upload: {remote_name} "
                f"expected sha256 {expected}, remote has {actual}"
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

    def clone(self) -> Transport:
        return LocalTransport(self.root)

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

    def move(self, remote_dir: str, remote_name: str, dest_dir: str, dest_name: str) -> None:
        self.makedirs(dest_dir)
        os.replace(self._remote(remote_dir, remote_name), self._remote(dest_dir, dest_name))

    def makedirs(self, remote_dir: str) -> None:
        (self.root / remote_dir).mkdir(parents=True, exist_ok=True)


class FtpTransport(Transport):
    kind = "ftp"

    def __init__(
        self, host: str, port: int = 21, username: str = "", password: str = "", timeout: int = 30
    ):
        self._connect_args = (host, port, username, password, timeout)
        self.ftp = ftplib.FTP()
        self.ftp.connect(host, port, timeout=timeout)
        self.ftp.login(username, password)
        try:
            self._home = self.ftp.pwd()
        except (ftplib.Error, OSError) as _:
            self._home = "/"

    def clone(self) -> Transport:
        return FtpTransport(*self._connect_args)

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

    def move(self, remote_dir: str, remote_name: str, dest_dir: str, dest_name: str) -> None:
        self.makedirs(dest_dir)
        # Paths are relative to the login directory (the convention
        # every other method here uses); reset cwd there first, so a
        # previous cwd() cannot mis-resolve them.
        self.ftp.cwd(self._home)
        self.ftp.rename(f"{remote_dir}/{remote_name}", f"{dest_dir}/{dest_name}")

    def makedirs(self, remote_dir: str) -> None:
        self.ftp.cwd(self._home)
        for part in remote_dir.split("/"):
            if not part or part == ".":
                continue
            try:
                self.ftp.cwd(part)
            except (ftplib.error_perm, OSError) as _:
                try:
                    self.ftp.mkd(part)
                except (ftplib.error_perm, OSError) as _:
                    pass
                self.ftp.cwd(part)

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
        self._connect_args = (host, port, username, password, key_file, timeout)
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

    def clone(self) -> Transport:
        return SftpTransport(*self._connect_args)

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

    def move(self, remote_dir: str, remote_name: str, dest_dir: str, dest_name: str) -> None:
        self.makedirs(dest_dir)
        self.sftp.rename(f"{remote_dir}/{remote_name}", f"{dest_dir}/{dest_name}")

    def makedirs(self, remote_dir: str) -> None:
        path = ""
        for part in remote_dir.split("/"):
            if not part or part == ".":
                continue
            path = f"{path}/{part}" if path else part
            try:
                self.sftp.mkdir(path)
            except OSError:
                pass

    def close(self) -> None:
        try:
            self.sftp.close()
            self._client.close()
        except (OSError, Exception) as e:  # noqa: BLE001 -- close() must never raise
            log.debug("sftp close failed (ignored): %s", e)


def resolve_password(cfg: TransportConfig) -> str:
    """Password for an FTP/SFTP connection. The env var named in
    ``password_env`` (default FILEFLOW_FTP_PASSWORD) wins when set;
    a literal ``password`` in the config is the fallback. The literal
    is less safe -- it sits in a file -- so prefer the env var."""
    return os.environ.get(cfg.password_env or "FILEFLOW_FTP_PASSWORD") or cfg.password or ""


def resolve_key_file(cfg: TransportConfig) -> str | None:
    """SFTP key file: the env var named in ``key_file_env`` (default
    FILEFLOW_SFTP_KEY_FILE) wins; a literal ``key_file`` is the fallback."""
    return os.environ.get(cfg.key_file_env or "FILEFLOW_SFTP_KEY_FILE") or cfg.key_file


def build_transport(cfg: TransportConfig) -> Transport:
    """Build a transport from a parsed transport config.

    Password / key come from env vars first (FILEFLOW_FTP_PASSWORD /
    FILEFLOW_SFTP_KEY_FILE, or the names in ``password_env`` /
    ``key_file_env``); a literal ``password`` / ``key_file`` in the
    config is the fallback.
    """
    if cfg.kind == "local":
        return LocalTransport(cfg.root)
    if cfg.kind == "ftp":
        return FtpTransport(cfg.host, cfg.port, cfg.username, resolve_password(cfg))
    if cfg.kind == "sftp":
        return SftpTransport(
            cfg.host,
            cfg.port,
            cfg.username,
            resolve_password(cfg) or None,
            resolve_key_file(cfg),
        )
    raise ValueError(f"unknown transport {cfg.kind!r} (want local|ftp|sftp)")
