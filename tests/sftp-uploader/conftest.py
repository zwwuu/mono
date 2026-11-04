"""Pytest fixtures: a real (stub) SFTP server on localhost."""
import paramiko
import pytest

from stub_sftp import StubSFTPServer


@pytest.fixture(scope="session")
def host_key():
    return paramiko.RSAKey.generate(2048)


@pytest.fixture
def sftp_server(tmp_path, host_key):
    root = tmp_path / "srv"
    root.mkdir()
    server = StubSFTPServer(str(root), host_key, password="secret")
    server.start()
    yield server
    server.stop()
    server.join(timeout=10)
