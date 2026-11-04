"""Pipeline steps (SFTP transport via paramiko).

Each step is a :class:`Step` subclass: set ``name`` and implement ``run``.
Steps use the shared static :mod:`common.logging` logger directly and share
state through a :class:`sftp_uploader.context.RunContext`.
"""
from __future__ import annotations

import glob
import os
import posixpath
from datetime import datetime

from common.logging import log

from .context import RunContext


class Step:
    """One pipeline stage. The pipeline attributes any exception raised
    by ``run`` to this step's ``name``."""

    name = "step"

    def run(self, ctx: RunContext) -> None:
        raise NotImplementedError


class FindFileStep(Step):
    """Locate the local file. ``file.pattern`` may contain strftime codes
    (e.g. ``report-%Y%m%d.csv``) which are expanded with today's date,
    then treated as a glob. ``file.pick`` chooses among several matches."""

    name = "find_file"

    def run(self, ctx: RunContext) -> None:
        cfg = ctx.config["file"]

        pattern = datetime.now().strftime(cfg["pattern"])
        log.debug("find_file: pattern after date expansion: %r", pattern)
        search = os.path.join(cfg["local_dir"], pattern)
        matches = sorted(glob.glob(search))
        log.info("find_file: %d file(s) matched %s", len(matches), search)
        if not matches:
            raise FileNotFoundError(f"no files matched {search!r}")

        pick = cfg.get("pick", "newest")
        if pick == "newest":
            chosen = max(matches, key=os.path.getmtime)
        elif pick == "oldest":
            chosen = min(matches, key=os.path.getmtime)
        else:  # "first"
            chosen = matches[0]

        ctx.local_path = chosen
        ctx.filename = os.path.basename(chosen)
        log.info("find_file: picked %s (%d bytes) via pick=%r",
                 chosen, os.path.getsize(chosen), pick)


class ConnectStep(Step):
    """Open the SSH connection and start an SFTP session.

    Auth is fully config-driven (no SSH agent, no ~/.ssh key discovery):
    ``sftp.key_file`` selects key auth, otherwise password auth.
    """

    name = "connect"

    @staticmethod
    def _load_key(scfg: dict):
        """Load the private key from ``sftp.key_file`` (RSA/Ed25519/ECDSA)."""
        import paramiko

        path = os.path.expanduser(scfg["key_file"])
        passphrase = scfg.get("key_passphrase")
        last_exc = None
        for cls in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey):
            try:
                key = cls.from_private_key_file(path, password=passphrase)
                log.info("connect: loaded %s key from %s", cls.__name__, path)
                return key
            except paramiko.SSHException as exc:
                last_exc = exc
        raise ValueError(f"could not load private key {path!r}: {last_exc}")

    def run(self, ctx: RunContext) -> None:
        import paramiko

        scfg = ctx.config["sftp"]

        ssh = paramiko.SSHClient()
        policy = scfg.get("host_key_policy", "auto_add")
        if policy == "auto_add":
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        elif policy == "reject":
            ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
        else:
            raise ValueError(f"sftp.host_key_policy must be 'auto_add' or 'reject', got {policy!r}")

        kwargs = {
            "hostname": scfg["host"],
            "port": int(scfg.get("port", 22)),
            "username": scfg["username"],
            "timeout": int(scfg.get("timeout", 30)),
            "banner_timeout": int(scfg.get("timeout", 30)),
            "allow_agent": False,
            "look_for_keys": False,
        }
        log.info("connect: connecting to %s:%s (timeout=%ss, host_key_policy=%s) ...",
                 kwargs["hostname"], kwargs["port"], kwargs["timeout"], policy)
        if scfg.get("key_file"):
            kwargs["pkey"] = self._load_key(scfg)
            auth = "key"
        else:
            kwargs["password"] = scfg.get("password") or ""
            auth = "password"

        ssh.connect(**kwargs)
        ctx.ssh = ssh
        ctx.sftp = ssh.open_sftp()
        log.info("connect: %s@%s:%s (sftp, %s auth) — session open",
                 scfg["username"], scfg["host"], kwargs["port"], auth)


class UploadStep(Step):
    """Upload the file to the remote directory via SFTP.

    If the remote file already exists, ``sftp.on_existing`` decides:
    ``overwrite`` (default), ``skip``, or ``fail``.
    """

    name = "upload"

    @staticmethod
    def _remote_path(scfg: dict, filename: str) -> str:
        remote_dir = (scfg.get("remote_dir") or "").strip()
        if not remote_dir or remote_dir == "/":
            return "/" + filename if remote_dir == "/" else filename
        return posixpath.join(remote_dir.rstrip("/"), filename)

    @staticmethod
    def _ensure_remote_dir(sftp, remote_dir: str, create: bool) -> None:
        """Ensure remote_dir exists, creating parents as needed when allowed."""
        remote_dir = (remote_dir or "").strip()
        if not remote_dir or remote_dir in ("/", "."):
            return
        try:
            sftp.stat(remote_dir)
            log.debug("upload: remote directory exists: %s", remote_dir)
            return
        except IOError:
            log.debug("upload: remote directory missing: %s", remote_dir)
        if not create:
            raise IOError(f"remote directory not accessible: {remote_dir!r}")

        prefix = "/" if remote_dir.startswith("/") else ""
        cur = ""
        for part in [p for p in remote_dir.split("/") if p]:
            cur = f"{prefix}{part}" if not cur else f"{cur}/{part}"
            try:
                sftp.stat(cur)
                log.debug("upload: remote directory exists: %s", cur)
            except IOError:
                sftp.mkdir(cur)
                log.info("upload: created remote directory: %s", cur)
        log.info("upload: remote directory ready: %s", remote_dir)

    def run(self, ctx: RunContext) -> None:
        sftp = ctx.sftp
        scfg = ctx.config["sftp"]

        self._ensure_remote_dir(sftp, scfg.get("remote_dir", ""), scfg.get("create_remote_dir", True))

        remote_path = self._remote_path(scfg, ctx.filename)
        ctx.remote_path = remote_path
        policy = scfg.get("on_existing", "overwrite")
        local_size = os.path.getsize(ctx.local_path)
        log.debug("upload: local=%s (%d bytes) remote=%s on_existing=%s",
                  ctx.local_path, local_size, remote_path, policy)

        try:
            sftp.stat(remote_path)
            exists = True
        except IOError:
            exists = False
        log.debug("upload: remote file exists=%s: %s", exists, remote_path)

        if exists:
            if policy == "skip":
                log.info("upload: %s already exists — skipping (on_existing=skip)", remote_path)
                ctx.skipped = True
                return
            if policy == "fail":
                raise FileExistsError(f"remote file already exists: {remote_path}")
            log.info("upload: %s already exists — overwriting", remote_path)

        log.info("upload: transferring %d bytes ...", local_size)
        sftp.put(ctx.local_path, remote_path)
        log.info("upload: %s -> %s:%s (%d bytes transferred)",
                 ctx.filename, scfg["host"], remote_path, local_size)


class VerifyStep(Step):
    """Compare the remote file size against the local size
    (skipped when ``verify`` is false in config)."""

    name = "verify"

    def run(self, ctx: RunContext) -> None:
        if not ctx.config.get("verify", True):
            log.info("verify: skipped (disabled in config)")
            return

        sftp = ctx.sftp
        remote_path = ctx.remote_path or UploadStep._remote_path(ctx.config["sftp"], ctx.filename)
        try:
            remote_size = sftp.stat(remote_path).st_size
        except IOError as exc:
            raise ValueError(f"verify: cannot stat remote file {remote_path!r}: {exc}")

        local_size = os.path.getsize(ctx.local_path)
        log.info("verify: local=%d bytes, remote=%d bytes", local_size, int(remote_size))
        if int(remote_size) != local_size:
            raise ValueError(f"size mismatch: local {local_size} != remote {remote_size}")
        log.info("verify: OK — sizes match (%d bytes)", local_size)
