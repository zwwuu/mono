"""Tests for common.sysinfo: runtime system, user, and network info.

Portable: they assert structure and types, never specific values, so
they pass on Windows (the primary target) and on Linux/macOS.
"""

import json
import re
import sys

import pytest

from common import sysinfo
from common.sysinfo import (
    all_info,
    network_info,
    runtime_info,
    system_info,
    user_info,
)

IS_WINDOWS = sys.platform == "win32"

MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")


def test_system_info_shape():
    info = system_info()
    for key in (
        "os",
        "os_release",
        "os_version",
        "machine",
        "processor",
        "cpu_count",
        "hostname",
        "uptime_seconds",
        "mem_total_bytes",
        "mem_available_bytes",
    ):
        assert key in info, key
    assert info["os"]
    assert isinstance(info["hostname"], str) and info["hostname"]
    assert info["cpu_count"] is None or isinstance(info["cpu_count"], int)
    assert info["uptime_seconds"] is None or info["uptime_seconds"] >= 0


@pytest.mark.skipif(not IS_WINDOWS, reason="Windows-only keys")
def test_system_info_windows_keys():
    info = system_info()
    assert info["os"] == "Windows"
    assert info["windows_release"]
    assert info["windows_build"]
    assert info["mem_total_bytes"] and info["mem_total_bytes"] > 0
    assert info["mem_available_bytes"] is not None


def test_user_info_shape():
    info = user_info()
    assert info["username"]
    assert info["home"]
    if IS_WINDOWS:
        assert "domain" in info


def test_network_info_shape():
    info = network_info()
    assert info["hostname"]
    assert info["fqdn"]
    assert isinstance(info["addresses"], list) and info["addresses"]
    assert all(isinstance(a, str) and a for a in info["addresses"])
    assert MAC_RE.match(info["mac"]), info["mac"]


def test_runtime_info_shape():
    info = runtime_info()
    assert info["python"]
    assert info["python_implementation"]
    assert info["executable"]
    assert isinstance(info["pid"], int) and info["pid"] > 0
    assert info["cwd"]


def test_all_info_merges_sections():
    info = all_info()
    assert set(info) == {"system", "user", "network", "runtime"}
    assert info["system"] == system_info()
    assert info["network"] == network_info()


def test_all_info_is_json_serializable():
    json.dumps(all_info())


def test_windows_helpers_degrade_off_windows():
    if IS_WINDOWS:
        pytest.skip("Windows-only behavior")
    assert sysinfo._windows_uptime_seconds() is None
    assert sysinfo._windows_memory_bytes() == (None, None)
