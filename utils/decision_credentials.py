"""Write-only decision credentials encrypted for the current Windows user."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

from utils.paths import get_user_data_dir


def _path() -> Path:
    return get_user_data_dir() / "decision_api_key.dpapi"


def _crypt(data: bytes, *, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise RuntimeError("credential_store_requires_windows")

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = Blob(len(data), buffer)
    target = Blob()
    library = ctypes.WinDLL("crypt32", use_last_error=True)
    operation = library.CryptUnprotectData if decrypt else library.CryptProtectData
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise RuntimeError("credential_store_unavailable")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


def save_api_key(value: str):
    key = str(value or "").strip()
    if not key or len(key) > 512 or any(char.isspace() for char in key):
        raise ValueError("invalid_api_key")
    encrypted = _crypt(key.encode("utf-8"))
    path = _path()
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_bytes(encrypted)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def clear_api_key():
    _path().unlink(missing_ok=True)


def get_api_key() -> str:
    path = _path()
    if path.exists():
        try:
            return _crypt(path.read_bytes(), decrypt=True).decode("utf-8").strip()
        except (OSError, UnicodeError, RuntimeError):
            raise RuntimeError("credential_unreadable") from None
    return os.environ.get("DECISION_API_KEY", "").strip()


def credential_state() -> dict:
    try:
        configured = bool(get_api_key())
        return {"api_key_configured": configured,
                "api_key_source": "saved" if _path().exists() else "environment" if configured else "none",
                "api_key_error": ""}
    except RuntimeError:
        return {"api_key_configured": False, "api_key_source": "saved", "api_key_error": "credential_unreadable"}
