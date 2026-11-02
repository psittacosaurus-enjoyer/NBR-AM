"""Atomic JSON snapshots and bounded Windows file replacement recovery."""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
import uuid
import weakref


_WINDOWS = os.name == "nt"
_REPLACE_ATTEMPTS = 20
_REPLACE_DELAY_SECONDS = .05
_WINDOWS_CONTENTION_ERRORS = frozenset({5, 32, 33})
_LOCKS = weakref.WeakValueDictionary()
_LOCKS_GUARD = threading.Lock()

if _WINDOWS:
    import ctypes
    from ctypes import wintypes
    import msvcrt

    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _create_file = _kernel.CreateFileW
    _create_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                             wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                             wintypes.HANDLE]
    _create_file.restype = wintypes.HANDLE
    _close_handle = _kernel.CloseHandle
    _close_handle.argtypes = [wintypes.HANDLE]
    _close_handle.restype = wintypes.BOOL
    _INVALID_HANDLE = ctypes.c_void_p(-1).value


def _path_lock(path):
    """Coordinate our short-lived readers/replacers without retaining old paths."""
    key = os.path.normcase(os.path.abspath(os.fspath(path)))
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


def _open_binary_reader(path):
    """Open without unnecessarily denying Windows file sharing."""
    path = Path(path)
    if not _WINDOWS:
        return path.open("rb")
    # Share read, write and delete. Windows can still delay replacement of an
    # open destination, so callers also keep the handle short and coordinate.
    handle = _create_file(str(path), 0x80000000, 0x1 | 0x2 | 0x4,
                          None, 3, 0x80, None)
    if handle == _INVALID_HANDLE:
        error = ctypes.WinError(ctypes.get_last_error())
        error.filename = str(path)
        raise error
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        _close_handle(handle)
        raise
    try:
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def read_json_file(path):
    """Read one complete snapshot; preserve missing, access and parse errors."""
    with _path_lock(path):
        with _open_binary_reader(path) as stream:
            payload = stream.read()
    return json.loads(payload.decode("utf-8"))


def replace_atomic(temp, path):
    """Replace without unlinking the destination; retry Windows contention only.

    The caller owns the temporary file and its cleanup. Persistent access errors
    remain errors after at most 20 attempts and 950 ms of retry delays.
    """
    with _path_lock(path):
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(temp, path)
                return
            except OSError as error:
                if (not _WINDOWS
                        or getattr(error, "winerror", None) not in _WINDOWS_CONTENTION_ERRORS
                        or attempt == _REPLACE_ATTEMPTS - 1):
                    raise
                time.sleep(_REPLACE_DELAY_SECONDS)


def atomic_json(path, value, *, indent=None):
    """Publish JSON only after its unique temporary snapshot is fsynced/valid.

    Validation uses JSON serialization semantics (for example, tuples become
    arrays). It never compares the destination with this writer's value after
    publication: another writer may already have published a newer snapshot.
    """
    payload = json.dumps(value, indent=indent, ensure_ascii=False, allow_nan=False)
    expected = json.loads(payload)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp")
    owns_temp = False
    try:
        with temp.open("x", encoding="utf-8") as stream:
            owns_temp = True
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if read_json_file(temp) != expected:
            raise OSError("Atomic JSON temporary snapshot verification failed")
        replace_atomic(temp, path)
    finally:
        if owns_temp:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                # Cleanup cannot turn a successful publication into failure or
                # obscure the error that prevented authoritative publication.
                pass
