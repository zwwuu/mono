# FileFlow — transfer backends: HTTP download, FTP (real or demo), SMB copy.
import os
import shutil
import urllib.request
from ftplib import FTP

from ..config import env_secret


class TransferError(Exception):
    pass


# ---------------------------------------------------------------- download
def download_url(url, dest, timeout=60):
    """Download http/https/file URL to dest, streaming. Returns byte count."""
    req = urllib.request.Request(url, headers={"User-Agent": "FileFlow/1.0"})
    total = 0
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as out:
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            out.write(chunk)
            total += len(chunk)
    return total


# ------------------------------------------------------------- picking files
def pick_entry(entries, pattern="*", sort="newest"):
    """Pick one file from [(name, mtime_or_None)] candidates.

    pattern is a glob (fnmatch); sort is "newest" (largest mtime),
    "oldest" (smallest mtime), or "name" (alphabetical). Ties — including
    entries without an mtime — break alphabetically, so the pick is
    always deterministic. Raises TransferError when nothing matches.
    """
    import fnmatch

    candidates = [(n, m) for n, m in entries if fnmatch.fnmatch(n, pattern or "*")]
    if not candidates:
        raise TransferError(f"no file matches pattern {pattern!r}")
    if sort == "name":
        return sorted(candidates, key=lambda e: e[0])[0][0]
    if sort == "oldest":
        return sorted(candidates, key=lambda e: (e[1] if e[1] is not None else float("inf"), e[0]))[
            0
        ][0]
    if sort == "newest":
        return sorted(
            candidates, key=lambda e: (-e[1] if e[1] is not None else float("inf"), e[0])
        )[0][0]
    raise TransferError(f"unknown sort: {sort!r}")


def pick_from_local_dir(dir_path, pattern="*", sort="newest"):
    """Pick one file from a local directory. Returns (name, full_path)."""
    if not os.path.isdir(dir_path):
        raise TransferError(f"local source directory missing: {dir_path}")
    entries = []
    for entry in os.scandir(dir_path):
        if entry.is_file():
            entries.append((entry.name, entry.stat().st_mtime))
    name = pick_entry(entries, pattern, sort)
    return name, os.path.join(dir_path, name)


# --------------------------------------------------------------------- FTP
class FtpBackend:
    """Real FTP server backend (ftplib)."""

    def __init__(self, cfg):
        ftp = cfg.raw.get("ftp", {})
        self.host = ftp.get("host", "")
        self.port = int(ftp.get("port", 21))
        self.username = ftp.get("username", "anonymous")
        self.password = env_secret("FILEFLOW_FTP_PASSWORD") or ftp.get("password", "")
        self.timeout = int(ftp.get("timeout_seconds", 30))
        self._ftp = None

    def __enter__(self):
        self._ftp = FTP()
        self._ftp.connect(self.host, self.port, timeout=self.timeout)
        self._ftp.login(self.username, self.password)
        return self

    def __exit__(self, *exc):
        try:
            self._ftp.quit()
        except Exception:
            pass

    def upload(self, local_path, remote_dir, remote_name):
        self._ftp.cwd("/")
        for part in remote_dir.strip("/").split("/"):
            if not part:
                continue
            try:
                self._ftp.cwd(part)
            except Exception:
                self._ftp.mkd(part)
                self._ftp.cwd(part)
        with open(local_path, "rb") as f:
            self._ftp.storbinary(f"STOR {remote_name}", f)

    def download(self, remote_dir, remote_name, local_path):
        self._ftp.cwd("/" + remote_dir.strip("/"))
        with open(local_path, "wb") as f:
            self._ftp.retrbinary(f"RETR {remote_name}", f.write)

    def list_names(self, remote_dir):
        self._ftp.cwd("/" + remote_dir.strip("/"))
        return self._ftp.nlst()

    def list_entries(self, remote_dir):
        """[(name, mtime_or_None)] for files in remote_dir.

        Uses MLSD when the server supports it; falls back to NLST with
        unknown mtimes (pick_entry then orders by name).
        """
        self._ftp.cwd("/" + remote_dir.strip("/"))
        try:
            entries = []
            for name, facts in self._ftp.mlsd():
                if facts.get("type", "file") != "file":
                    continue
                mtime = None
                raw = facts.get("modify", "")
                if len(raw) >= 14:
                    try:
                        import calendar
                        import time

                        mtime = calendar.timegm(time.strptime(raw[:14], "%Y%m%d%H%M%S"))
                    except (ValueError, TypeError) as _e:
                        mtime = None
                entries.append((name, mtime))
            return entries
        except Exception:
            return [(n, None) for n in self._ftp.nlst()]


class DemoFtpBackend:
    """Local-directory stand-in for an FTP server (demo mode).

    inbox/processed live under <data_dir>/demo_ftp/. The demo 'processor'
    (demo.py) watches the inbox and drops results into processed/.
    """

    def __init__(self, cfg):
        self.root = cfg.path("demo_ftp")

    def _dir(self, remote_dir):
        # remote_dir like "inbox" or "processed" maps onto the local tree
        return os.path.join(self.root, remote_dir.strip("/"))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def upload(self, local_path, remote_dir, remote_name):
        d = self._dir(remote_dir)
        os.makedirs(d, exist_ok=True)
        shutil.copyfile(local_path, os.path.join(d, remote_name))

    def download(self, remote_dir, remote_name, local_path):
        src = os.path.join(self._dir(remote_dir), remote_name)
        if not os.path.exists(src):
            raise TransferError(f"demo ftp: {remote_name} not in {remote_dir}")
        shutil.copyfile(src, local_path)

    def list_names(self, remote_dir):
        d = self._dir(remote_dir)
        if not os.path.isdir(d):
            return []
        return sorted(os.listdir(d))

    def list_entries(self, remote_dir):
        d = self._dir(remote_dir)
        if not os.path.isdir(d):
            return []
        return [(entry.name, entry.stat().st_mtime) for entry in os.scandir(d) if entry.is_file()]


def open_ftp(cfg):
    if cfg.get("demo", "enabled", False):
        return DemoFtpBackend(cfg)
    return FtpBackend(cfg)


def download_from_ftp(cfg, remote_dir, pattern, sort, dest_dir):
    """Pick one file from an FTP directory and download it.

    Returns (name, local_path, size). In demo mode remote_dir maps onto
    <data_dir>/demo_ftp/<remote_dir>.
    """
    os.makedirs(dest_dir, exist_ok=True)
    with open_ftp(cfg) as ftp:
        name = pick_entry(ftp.list_entries(remote_dir or ""), pattern, sort)
        dest = os.path.join(dest_dir, name)
        ftp.download(remote_dir or "", name, dest)
    return name, dest, os.path.getsize(dest)


# --------------------------------------------------------------------- SMB
def copy_to_smb(cfg, local_path, remote_name, subdir=""):
    """Copy a file to the distribution share.

    Modes (config [smb] mode):
      - "path": target_dir is a local or mounted-share path (e.g. /mnt/share
        or \\\\server\\share mounted by the OS). Plain file copy.
      - "smbprotocol": use the smbprotocol library (pip install smbprotocol)
        with server/share/path + credentials from env FILEFLOW_SMB_PASSWORD.
    `subdir` appends a per-task subdirectory under the configured target.
    Returns a description of where the file landed.
    """
    smb = cfg.raw.get("smb", {})
    mode = smb.get("mode", "path")
    if cfg.get("demo", "enabled", False):
        dest_dir = cfg.path("demo_smb", subdir) if subdir else cfg.path("demo_smb")
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, remote_name)
        shutil.copyfile(local_path, dest)
        return f"demo smb dir {dest}"

    if mode == "path":
        dest_dir = smb.get("target_dir", "")
        if not dest_dir:
            raise TransferError("smb.target_dir is empty")
        if subdir:
            dest_dir = os.path.join(dest_dir, subdir)
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, remote_name)
        shutil.copyfile(local_path, dest)
        return dest

    if mode == "smbprotocol":
        try:
            import smbclient  # from smbprotocol package
        except ImportError as e:
            raise TransferError("smb mode=smbprotocol requires: pip install smbprotocol") from e
        server = smb.get("server", "")
        share = smb.get("share", "")
        remote_dir = "/".join(p for p in (smb.get("path", ""), subdir) if p)
        username = smb.get("username", "")
        password = env_secret("FILEFLOW_SMB_PASSWORD") or smb.get("password", "")
        smbclient.register_session(server, username=username, password=password)
        dest = f"\\\\{server}\\{share}\\{remote_dir}\\{remote_name}".replace("/", "\\")
        with open(local_path, "rb") as src, smbclient.open_file(dest, mode="wb") as dst:
            shutil.copyfileobj(src, dst)
        return dest

    raise TransferError(f"unknown smb mode: {mode}")


def download_from_smb(cfg, subdir, pattern, sort, dest_dir):
    """Pick one file from the distribution share and download it.

    Mirrors copy_to_smb's modes: demo mode reads <data_dir>/demo_smb/,
    "path" mode reads target_dir, "smbprotocol" reads via smbclient.
    Returns (name, local_path, size).
    """
    os.makedirs(dest_dir, exist_ok=True)
    if cfg.get("demo", "enabled", False):
        root = cfg.path("demo_smb", subdir) if subdir else cfg.path("demo_smb")
        name, src = pick_from_local_dir(root, pattern, sort)
        dest = os.path.join(dest_dir, name)
        shutil.copyfile(src, dest)
        return name, dest, os.path.getsize(dest)

    smb = cfg.raw.get("smb", {})
    mode = smb.get("mode", "path")
    if mode == "path":
        root = smb.get("target_dir", "")
        if not root:
            raise TransferError("smb.target_dir is empty")
        if subdir:
            root = os.path.join(root, subdir)
        name, src = pick_from_local_dir(root, pattern, sort)
        dest = os.path.join(dest_dir, name)
        shutil.copyfile(src, dest)
        return name, dest, os.path.getsize(dest)

    if mode == "smbprotocol":
        try:
            import smbclient
        except ImportError as e:
            raise TransferError("smb mode=smbprotocol requires: pip install smbprotocol") from e
        server = smb.get("server", "")
        share = smb.get("share", "")
        remote_dir = "/".join(p for p in (smb.get("path", ""), subdir) if p)
        username = smb.get("username", "")
        password = env_secret("FILEFLOW_SMB_PASSWORD") or smb.get("password", "")
        smbclient.register_session(server, username=username, password=password)
        base = f"\\\\{server}\\{share}" + (f"\\{remote_dir}" if remote_dir else "")
        entries = []
        for name in smbclient.listdir(base):
            full = base + "\\" + name
            try:
                if smbclient.path.isdir(full):
                    continue
                mtime = smbclient.stat(full).st_mtime
            except Exception:
                continue
            entries.append((name, mtime))
        name = pick_entry(entries, pattern, sort)
        dest = os.path.join(dest_dir, name)
        with smbclient.open_file(base + "\\" + name, mode="rb") as src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out)
        return name, dest, os.path.getsize(dest)

    raise TransferError(f"unknown smb mode: {mode}")
