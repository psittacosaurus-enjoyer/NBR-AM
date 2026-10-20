"""Read Windows process creation identities without controlling any process."""
from __future__ import annotations

import ctypes
from ctypes import wintypes


def process_identity(pid):
    """Return PID + creation ticks and Unix start time; reject invalid PIDs."""
    if type(pid) is not int or pid <= 0:
        raise ValueError("Invalid process ID")
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        raise OSError("Cannot establish farming process creation identity")
    try:
        values = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in values)):
            raise OSError("Cannot read farming process creation time")
        ticks = (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime
        return f"{pid}:{ticks}", ticks / 10_000_000 - 11_644_473_600
    finally:
        kernel.CloseHandle(handle)
