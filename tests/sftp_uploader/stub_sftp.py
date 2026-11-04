"""In-process stub SFTP server for tests.

Speaks the real SFTP protocol over 127.0.0.1 on an ephemeral port, rooted at
a temp directory. Lets the test suite exercise the actual ``connect`` /
``upload`` / ``verify`` step code with no production (or even local sshd)
server available.

``server.state["fail_puts"] = N`` makes the next N uploads fail, to test
the retry logic.
"""
from __future__ import annotations

import errno
import os
import socket
import threading
import time

import paramiko
from paramiko.sftp import SFTP_OK


class StubHandle(paramiko.SFTPHandle):
    def __init__(self, flags, fobj):
        super().__init__(flags)
        self._fobj = fobj

    def close(self):
        try:
            self._fobj.close()
        finally:
            super().close()

    def read(self, offset, length):
        self._fobj.seek(offset)
        return self._fobj.read(length)

    def write(self, offset, data):
        self._fobj.seek(offset)
        self._fobj.write(data)
        self._fobj.flush()
        return SFTP_OK

    def stat(self):
        return paramiko.SFTPAttributes.from_stat(os.fstat(self._fobj.fileno()))


class StubSFTPInterface(paramiko.SFTPServerInterface):
    def __init__(self, server, root, state):
        super().__init__(server)
        self.root = root
        self.state = state

    def _abspath(self, path):
        rel = path.lstrip("/")
        p = os.path.normpath(os.path.join(self.root, rel))
        if p != self.root and not p.startswith(self.root + os.sep):
            raise OSError(errno.EACCES, "path escapes server root")
        return p

    def stat(self, path):
        return paramiko.SFTPAttributes.from_stat(os.stat(self._abspath(path)))

    lstat = stat

    def mkdir(self, path, attr):
        os.mkdir(self._abspath(path))
        return SFTP_OK

    def rmdir(self, path):
        os.rmdir(self._abspath(path))
        return SFTP_OK

    def open(self, path, flags, attr):
        # NOTE: flags are os.open() flags — SFTPServer._convert_pflags already
        # translated the SFTP wire flags, applying CREAT/TRUNC/EXCL semantics
        # via os.open itself.
        writing = bool(flags & (os.O_WRONLY | os.O_RDWR))
        if self.state.get("fail_puts", 0) > 0 and writing:
            self.state["fail_puts"] -= 1
            raise OSError(errno.ECONNRESET, "simulated drop")
        p = self._abspath(path)
        fd = os.open(p, flags, 0o644)
        if flags & os.O_RDWR:
            mode = "r+b"
        elif writing:
            mode = "wb"
        else:
            mode = "rb"
        return StubHandle(flags, os.fdopen(fd, mode))

    def remove(self, path):
        os.remove(self._abspath(path))
        return SFTP_OK

    def rename(self, oldpath, newpath):
        os.rename(self._abspath(oldpath), self._abspath(newpath))
        return SFTP_OK


class StubSSHServer(paramiko.ServerInterface):
    """Auth decisions read live from the StubSFTPServer, so tests can switch
    between password and key auth after the fixture is created."""

    def __init__(self, get_auth):
        self._get_auth = get_auth

    def check_channel_request(self, kind, chanid):
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    # NOTE: do NOT override check_channel_subsystem_request — the default
    # implementation dispatches to the handler registered with
    # Transport.set_subsystem_handler (which constructs and starts SFTPServer).

    def check_auth_password(self, username, password):
        pw, _ = self._get_auth()
        if pw is not None and password == pw:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_auth_publickey(self, username, key):
        _, blob = self._get_auth()
        if blob is not None and key.asbytes() == blob:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "password,publickey"


class StubSFTPServer(threading.Thread):
    """A real SFTP server on 127.0.0.1:<ephemeral port>, rooted at *root*."""

    def __init__(self, root, host_key, password="secret", pubkey_blob=None):
        super().__init__(daemon=True)
        self.root = root
        self.host_key = host_key
        self.password = password
        self.pubkey_blob = pubkey_blob
        self.state: dict = {}
        self._stop_event = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(5)
        self._sock.settimeout(0.5)
        self.port = self._sock.getsockname()[1]

    def run(self):
        while not self._stop_event.is_set():
            try:
                conn, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        transport = paramiko.Transport(conn)
        transport.add_server_key(self.host_key)
        transport.set_subsystem_handler(
            "sftp", paramiko.SFTPServer, StubSFTPInterface, self.root, self.state
        )
        server = StubSSHServer(lambda: (self.password, self.pubkey_blob))
        try:
            transport.start_server(server=server)
        except paramiko.SSHException:
            transport.close()
            return
        chan = transport.accept(20)
        if chan is None:
            transport.close()
            return
        while transport.is_active():
            time.sleep(0.2)
        transport.close()

    def stop(self):
        self._stop_event.set()
        try:
            self._sock.close()
        except OSError:
            pass
