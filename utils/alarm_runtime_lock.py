import ctypes
import os
import tempfile
import threading
from ctypes import wintypes

_NAME = "LianxinAlarmRuntime"
_handle = None
_lock_file = None
_guard = threading.Lock()


def _acquire() -> bool:
    global _handle, _lock_file
    if _handle is not None or _lock_file is not None:
        return False
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_mutex = kernel32.CreateMutexW
        create_mutex.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        create_mutex.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = create_mutex(None, False, f"Local\\{_NAME}")
        if not handle:
            return False
        if ctypes.get_last_error() == 183:
            kernel32.CloseHandle(handle)
            return False
        _handle = handle
        return True
    try:
        import fcntl
        _lock_file = open(os.path.join(tempfile.gettempdir(), f"{_NAME}.lock"), "a+")
        fcntl.flock(_lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (ImportError, OSError):
        if _lock_file:
            _lock_file.close()
            _lock_file = None
        return False


def _release() -> None:
    global _handle, _lock_file
    if _handle is not None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle(_handle)
        _handle = None
    if _lock_file is not None:
        import fcntl
        fcntl.flock(_lock_file.fileno(), fcntl.LOCK_UN)
        _lock_file.close()
        _lock_file = None


def acquire() -> bool:
    with _guard:
        return _acquire()


def release() -> None:
    with _guard:
        _release()
