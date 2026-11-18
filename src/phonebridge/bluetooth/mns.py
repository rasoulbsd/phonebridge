"""MAP notification server: the iPhone connects back here to report new messages."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from winrt.windows.devices.bluetooth.rfcomm import RfcommServiceId, RfcommServiceProvider
from winrt.windows.networking.sockets import SocketProtectionLevel, StreamSocketListener
from winrt.windows.storage.streams import DataWriter

from phonebridge.bluetooth.stream import WinRtStream
from phonebridge.protocol import (
    CLIENT_FEATURES,
    MAP_PROFILE_UUID16,
    MAP_VERSION,
    MNS_TARGET,
    MNS_UUID,
    ObexServer,
    StreamClosedError,
    events_from_put,
    sdp,
)

if TYPE_CHECKING:
    from winrt.windows.networking.sockets import StreamSocket, StreamSocketListenerConnectionReceivedEventArgs
    from winrt.windows.storage.streams import IBuffer

    from phonebridge.protocol import MapEvent, PutRequest

logger = logging.getLogger(__name__)

_ATTR_SERVICE_NAME = 0x0100
_ATTR_PROFILE_DESCRIPTORS = 0x0009
_ATTR_MAP_FEATURES = 0x0317


def _ibuffer(data: bytes) -> IBuffer:
    writer = DataWriter()
    writer.write_bytes(data)
    return writer.detach_buffer()


class MnsServer:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._events: asyncio.Queue[MapEvent] = asyncio.Queue()
        self._provider: RfcommServiceProvider | None = None
        self._listener: StreamSocketListener | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._active = 0

    @property
    def started(self) -> bool:
        return self._provider is not None

    @property
    def active(self) -> bool:
        return self._active > 0

    async def start(self) -> None:
        provider = await RfcommServiceProvider.create_async(RfcommServiceId.from_uuid(MNS_UUID))
        listener = StreamSocketListener()
        listener.add_connection_received(self._on_connection)
        await listener.bind_service_name_async(
            provider.service_id.as_string(),
            SocketProtectionLevel.BLUETOOTH_ENCRYPTION_ALLOW_NULL_AUTHENTICATION,
        )
        attributes = provider.sdp_raw_attributes
        attributes[_ATTR_SERVICE_NAME] = _ibuffer(sdp.text("MAP MNS-Phonebridge"))
        attributes[_ATTR_PROFILE_DESCRIPTORS] = _ibuffer(
            sdp.sequence(sdp.sequence(sdp.uuid16(MAP_PROFILE_UUID16), sdp.uint16(MAP_VERSION)))
        )
        attributes[_ATTR_MAP_FEATURES] = _ibuffer(sdp.uint32(CLIENT_FEATURES))
        provider.start_advertising(listener)
        self._provider, self._listener = provider, listener
        logger.info("notification service published")

    async def stop(self) -> None:
        if self._provider is not None:
            self._provider.stop_advertising()
        if self._listener is not None:
            self._listener.close()
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._provider = self._listener = None

    def drain(self) -> None:
        while not self._events.empty():
            self._events.get_nowait()

    async def next_event(self) -> MapEvent:
        return await self._events.get()

    def _on_connection(
        self,
        _sender: StreamSocketListener | None,
        args: StreamSocketListenerConnectionReceivedEventArgs | None,
    ) -> None:
        # Runs on a WinRT worker thread; hand the socket to the asyncio loop.
        if args is not None:
            self._loop.call_soon_threadsafe(self._spawn, args.socket)

    def _spawn(self, socket: StreamSocket) -> None:
        task = self._loop.create_task(self._serve(socket))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _serve(self, socket: StreamSocket) -> None:
        self._active += 1
        stream = WinRtStream(socket)
        logger.info("phone opened the notification channel")
        try:
            await ObexServer(stream, target=MNS_TARGET).serve(self._on_put)
        except (StreamClosedError, ValueError) as exc:
            logger.info("notification channel closed: %s", exc)
        finally:
            self._active -= 1
            with contextlib.suppress(OSError):
                await stream.close()

    async def _on_put(self, request: PutRequest) -> None:
        try:
            events = events_from_put(request)
        except ET.ParseError as exc:
            logger.warning("unreadable event report from phone: %s", exc)
            return
        for event in events:
            self._events.put_nowait(event)
