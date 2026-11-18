"""Windows DPAPI: encrypts secrets so only this Windows account on this PC can read them."""

from __future__ import annotations

import ctypes
from ctypes import wintypes


class _Blob(ctypes.Structure):
    _fields_ = (("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char)))


_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class DpapiError(OSError):
    pass


def _blob(data: bytes) -> tuple[_Blob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _take(blob: _Blob) -> bytes:
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob.pbData)


def protect(data: bytes) -> bytes:
    source, _keep = _blob(data)
    out = _Blob()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source), None, None, None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out)
    )
    if not ok:
        raise DpapiError(ctypes.GetLastError(), "could not encrypt the secret")
    return _take(out)


def unprotect(data: bytes) -> bytes:
    source, _keep = _blob(data)
    out = _Blob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out)
    )
    if not ok:
        raise DpapiError(ctypes.GetLastError(), "could not decrypt the secret")
    return _take(out)
