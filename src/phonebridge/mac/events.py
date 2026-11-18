"""Live events from BlueBubbles Server over Socket.IO."""

from __future__ import annotations

import asyncio
import functools
import logging
from typing import Protocol
from urllib.parse import quote

import socketio
from socketio.exceptions import ConnectionError as SocketConnectionError

from phonebridge.mac.client import BlueBubblesUnavailableError

logger = logging.getLogger(__name__)

DISCONNECTED = "_disconnected"
EVENTS = (
    "new-message",
    "updated-message",
    "message-send-error",
    "typing-indicator",
    "chat-read-status-changed",
    "group-name-change",
    "participant-added",
    "participant-removed",
    "participant-left",
    "group-icon-changed",
    "group-icon-removed",
    "ft-call-status-changed",
    "incoming-facetime",
)
_CONNECT_TIMEOUT = 15


class MacEvents(Protocol):
    async def connect(self) -> None: ...

    async def next(self) -> tuple[str, object]:
        """(event name, untrusted payload). DISCONNECTED when the link drops."""
        ...

    async def close(self) -> None: ...


class SocketIoEvents:
    def __init__(self, base_url: str, password: str) -> None:
        # The server authenticates the socket with the password in the handshake query.
        self._url = f"{base_url.rstrip('/')}?password={quote(password, safe='')}"
        self._queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
        self._sio = socketio.AsyncClient(reconnection=False, logger=False, engineio_logger=False)
        for name in EVENTS:
            self._sio.on(name, handler=functools.partial(self._push, name))
        self._sio.on("disconnect", handler=self._on_disconnect)

    async def _push(self, name: str, data: object = None) -> None:
        self._queue.put_nowait((name, data))

    async def _on_disconnect(self, *_reason: object) -> None:
        self._queue.put_nowait((DISCONNECTED, None))

    async def connect(self) -> None:
        try:
            await self._sio.connect(self._url, transports=["websocket"], wait_timeout=_CONNECT_TIMEOUT)
        except SocketConnectionError as exc:
            raise BlueBubblesUnavailableError(f"Live updates from the Mac failed: {exc}") from exc

    async def next(self) -> tuple[str, object]:
        return await self._queue.get()

    async def close(self) -> None:
        if self._sio.connected:
            await self._sio.disconnect()
