"""Saving and copying message attachments from the desktop window.

WebView2 inside pywebview cancels downloads and can't put files on the clipboard, so the window
calls these methods through pywebview's JavaScript bridge instead.
"""

from __future__ import annotations

import ctypes
import http.client
import io
import logging
import re
import shutil
import struct
import time
from ctypes import wintypes
from typing import TYPE_CHECKING, Protocol
from urllib.parse import quote, unquote, urlsplit

from PIL import Image, UnidentifiedImageError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

logger = logging.getLogger(__name__)

ATTACHMENT_PATH = "/api/attachments/"
_FETCH_TIMEOUT = 60.0
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = re.compile(r"^(con|prn|aux|nul|com\d|lpt\d)(\..*)?$", re.IGNORECASE)
_MAX_NAME = 120

_CF_DIB = 8
_CF_HDROP = 15
_GMEM_MOVEABLE = 0x0002
_DROPFILES_SIZE = 20
_CLIPBOARD_TRIES = 10
_CLIPBOARD_RETRY_DELAY = 0.05


class AttachmentError(Exception):
    """An attachment couldn't be fetched, saved or copied."""


class Fetcher(Protocol):
    def fetch(self, path: str) -> tuple[bytes, str]: ...


class Clipboard(Protocol):
    def put_files(self, paths: Sequence[Path], *, image_dib: bytes | None) -> None: ...


class SaveDialog(Protocol):
    def ask(self, suggested_name: str, directory: Path) -> Path | None: ...


def safe_filename(name: str, fallback: str = "attachment") -> str:
    """A name Windows accepts, keeping the extension."""
    cleaned = _UNSAFE.sub("_", name).strip().strip(".")
    if not cleaned or _RESERVED.match(cleaned):
        cleaned = f"{fallback}_{cleaned}" if cleaned else fallback
    if len(cleaned) > _MAX_NAME:
        stem, dot, ext = cleaned.rpartition(".")
        if dot and 0 < len(ext) <= 10:
            cleaned = f"{stem[: _MAX_NAME - len(ext) - 1]}.{ext}"
        else:
            cleaned = cleaned[:_MAX_NAME]
    return cleaned


def attachment_path(url: str) -> str:
    """The local server path for an attachment URL from the interface, or an error."""
    parts = urlsplit(url)
    if parts.scheme or parts.netloc or not parts.path.startswith(ATTACHMENT_PATH):
        raise AttachmentError("Not an attachment of this app.")
    attachment_id = unquote(parts.path.removeprefix(ATTACHMENT_PATH))
    if not attachment_id or "/" in attachment_id:
        raise AttachmentError("Not an attachment of this app.")
    # Always the full file: a thumbnail is never what someone means to save or copy.
    return ATTACHMENT_PATH + quote(attachment_id, safe="")


def dropfiles(paths: Sequence[Path]) -> bytes:
    """A CF_HDROP payload: the DROPFILES header and a double-NUL-ended UTF-16 path list."""
    header = struct.pack("<IiiII", _DROPFILES_SIZE, 0, 0, 0, 1)
    names = "".join(f"{path}\0" for path in paths) + "\0"
    return header + names.encode("utf-16-le")


def image_dib(data: bytes) -> bytes | None:
    """The picture as a CF_DIB (a BMP without its file header), or None if it isn't one Pillow reads."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            converted = image.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    out = io.BytesIO()
    converted.save(out, format="BMP")
    bmp_file_header = 14
    return out.getvalue()[bmp_file_header:]


class LocalServerFetcher:
    """Reads an attachment from Phonebridge's own server on this PC."""

    def __init__(self, port: int, token: str | None) -> None:
        self._port = port
        self._token = token

    def fetch(self, path: str) -> tuple[bytes, str]:
        target = f"{path}?token={quote(self._token, safe='')}" if self._token else path
        connection = http.client.HTTPConnection("127.0.0.1", self._port, timeout=_FETCH_TIMEOUT)
        try:
            connection.request("GET", target, headers={"Host": f"127.0.0.1:{self._port}"})
            response = connection.getresponse()
            body = response.read()
        except OSError as exc:
            raise AttachmentError("Couldn't load the attachment.") from exc
        finally:
            connection.close()
        if response.status != http.HTTPStatus.OK:
            raise AttachmentError("The attachment isn't available right now.")
        return body, response.getheader("Content-Type", "application/octet-stream")


class AttachmentFiles:
    """Save As and copy-to-clipboard for attachments, independent of the window toolkit."""

    def __init__(
        self,
        fetcher: Fetcher,
        clipboard: Clipboard,
        dialog: SaveDialog,
        *,
        clipboard_dir: Path,
        downloads_dir: Path,
    ) -> None:
        self._fetcher = fetcher
        self._clipboard = clipboard
        self._dialog = dialog
        self._clipboard_dir = clipboard_dir
        self._downloads_dir = downloads_dir

    def save(self, url: str, name: str) -> str | None:
        """Asks where to save, then writes the file. Returns the path, or None if cancelled."""
        path = attachment_path(url)
        target = self._dialog.ask(safe_filename(name), self._downloads_dir)
        if target is None:
            return None
        data, _ = self._fetcher.fetch(path)
        try:
            target.write_bytes(data)
        except OSError as exc:
            raise AttachmentError(f"Couldn't save to {target}.") from exc
        logger.info("attachment saved")
        return str(target)

    def copy(self, url: str, name: str) -> str:
        """Puts the file on the clipboard (and the picture itself, for images). Returns 'image' or 'file'."""
        path = attachment_path(url)
        data, content_type = self._fetcher.fetch(path)
        # Pasting in Explorer copies the file from here, so only the latest copy needs to stay.
        shutil.rmtree(self._clipboard_dir, ignore_errors=True)
        self._clipboard_dir.mkdir(parents=True, exist_ok=True)
        local = self._clipboard_dir / safe_filename(name)
        local.write_bytes(data)
        picture = image_dib(data) if content_type.startswith("image/") else None
        self._clipboard.put_files([local], image_dib=picture)
        return "image" if picture is not None else "file"


def _alloc_global(payload: bytes) -> int:
    kernel32 = ctypes.windll.kernel32
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    handle = kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(payload))
    if not handle:
        raise AttachmentError("Out of memory for the clipboard.")
    pointer = kernel32.GlobalLock(handle)
    ctypes.memmove(pointer, payload, len(payload))
    kernel32.GlobalUnlock(handle)
    return handle


class WindowsClipboard:
    """The Windows clipboard, holding files (CF_HDROP) and optionally a picture (CF_DIB)."""

    def put_files(self, paths: Sequence[Path], *, image_dib: bytes | None) -> None:
        user32 = ctypes.windll.user32
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.SetClipboardData.restype = wintypes.HANDLE
        user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        self._open(user32)
        try:
            user32.EmptyClipboard()
            formats = [(_CF_HDROP, dropfiles(paths))]
            if image_dib is not None:
                formats.append((_CF_DIB, image_dib))
            for kind, payload in formats:
                # On success the clipboard owns the memory; it must not be freed here.
                if not user32.SetClipboardData(kind, _alloc_global(payload)):
                    raise AttachmentError("Windows didn't accept the clipboard data.")
        finally:
            user32.CloseClipboard()

    @staticmethod
    def _open(user32: ctypes.WinDLL) -> None:
        # Another app can hold the clipboard for a moment; retry briefly before giving up.
        for _ in range(_CLIPBOARD_TRIES):
            if user32.OpenClipboard(None):
                return
            time.sleep(_CLIPBOARD_RETRY_DELAY)
        raise AttachmentError("The clipboard is busy. Try again.")
