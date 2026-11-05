"""Runtime system, user, and network info — stdlib only, Windows first.

Every function returns a plain JSON-serializable dict, so the result
can be logged, sent over the wire, or dumped to a file as-is.

Usage:
    from common.sysinfo import all_info, network_info, system_info, user_info

    print(system_info()["os"])        # "Windows"
    print(user_info()["username"])    # "zw"
    print(network_info()["addresses"])  # ["192.168.1.10", ...]

Windows is the primary target: OS build, CPU model, memory, and
uptime come from the Win32 API via ctypes. On other platforms the
same keys are returned with best-effort values (``None`` where the
platform has no stdlib way to provide them).
"""

import ctypes
import getpass
import os
import platform
import socket
import sys
import uuid

__all__ = ["all_info", "network_info", "runtime_info", "system_info", "user_info"]

_IS_WINDOWS = platform.system() == "Windows"


# -- Windows helpers (ctypes, no third-party deps) ---------------------------


def _windows_uptime_seconds():
    """Seconds since boot via GetTickCount64; None off Windows/on error."""
    if not _IS_WINDOWS:
        return None
    try:
        return ctypes.windll.kernel32.GetTickCount64() // 1000
    except Exception:  # noqa: BLE001 -- best-effort probe, any failure -> None
        return None


def _windows_memory_bytes():
    """(total, available) physical RAM in bytes; (None, None) off Windows."""
    if not _IS_WINDOWS:
        return None, None

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    try:
        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return status.ullTotalPhys, status.ullAvailPhys
    except Exception:  # noqa: BLE001 -- best-effort probe, any failure -> None
        return None, None


def _best_effort_uptime_seconds():
    """Uptime where the platform offers a stdlib way to get it."""
    if _IS_WINDOWS:
        return _windows_uptime_seconds()
    try:  # Linux
        with open("/proc/uptime", encoding="utf-8") as f:
            return int(float(f.read().split()[0]))
    except Exception:  # noqa: BLE001 -- best-effort probe, any failure -> None
        return None


# -- public API ---------------------------------------------------------------


def system_info():
    """OS, hardware, and boot info for the machine running this code."""
    uname = platform.uname()
    info = {
        "os": uname.system or None,  # "Windows", "Linux", "Darwin"
        "os_release": uname.release or None,
        "os_version": uname.version or None,
        "machine": uname.machine or None,  # "AMD64", "x86_64", "arm64"
        "processor": platform.processor() or None,
        "cpu_count": os.cpu_count(),
        "hostname": socket.gethostname() or None,
        "uptime_seconds": _best_effort_uptime_seconds(),
        "mem_total_bytes": None,
        "mem_available_bytes": None,
    }
    if _IS_WINDOWS:
        release, build, sp, _ptype = platform.win32_ver()
        info["windows_release"] = release or None  # "10", "11"
        info["windows_build"] = build or None  # "10.0.22631"
        info["windows_sp"] = sp or None
        info["cpu_model"] = os.environ.get("PROCESSOR_IDENTIFIER") or None
        total, avail = _windows_memory_bytes()
        info["mem_total_bytes"] = total
        info["mem_available_bytes"] = avail
    return info


def user_info():
    """Who is running this process."""
    try:
        username = getpass.getuser()
    except Exception:  # noqa: BLE001 -- best-effort probe, any failure -> None
        username = None
    info = {
        "username": username,
        "home": os.path.expanduser("~") or None,
    }
    if _IS_WINDOWS:
        info["domain"] = os.environ.get("USERDOMAIN") or None
        info["user_profile"] = os.environ.get("USERPROFILE") or None
    return info


def _ip_addresses():
    """All IP addresses the hostname resolves to (deduped, best effort)."""
    addresses = []
    try:
        for _family, _type, _proto, _canon, sockaddr in socket.getaddrinfo(
            socket.gethostname(), None
        ):
            ip = sockaddr[0]
            if ip and ip not in addresses:
                addresses.append(ip)
    except socket.gaierror:
        pass
    return addresses or ["127.0.0.1"]


def _mac_address():
    """Primary MAC as 'aa:bb:cc:dd:ee:ff' (best effort via uuid)."""
    node = uuid.getnode()
    return ":".join(f"{(node >> (8 * i)) & 0xFF:02x}" for i in reversed(range(6)))


def network_info():
    """Hostname, resolved IPs, and MAC for this machine."""
    return {
        "hostname": socket.gethostname() or None,
        "fqdn": socket.getfqdn() or None,
        "addresses": _ip_addresses(),
        "mac": _mac_address(),
    }


def runtime_info():
    """The Python runtime executing this code."""
    return {
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "executable": sys.executable or None,
        "pid": os.getpid(),
        "cwd": os.getcwd(),
        "argv0": sys.argv[0] if sys.argv else None,
    }


def all_info():
    """Everything above, grouped by section."""
    return {
        "system": system_info(),
        "user": user_info(),
        "network": network_info(),
        "runtime": runtime_info(),
    }
