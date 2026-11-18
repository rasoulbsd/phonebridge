"""Finds the paired iPhone and opens MAP / PBAP sessions over WinRT Bluetooth."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from winrt.windows.devices.bluetooth import BluetoothCacheMode, BluetoothDevice, BluetoothError
from winrt.windows.devices.bluetooth.rfcomm import RfcommServiceId
from winrt.windows.devices.enumeration import DeviceInformation
from winrt.windows.networking.sockets import SocketProtectionLevel, StreamSocket

from phonebridge.bluetooth.mns import MnsServer
from phonebridge.bluetooth.stream import WinRtStream
from phonebridge.domain import Contact
from phonebridge.protocol import (
    MAS_UUID,
    PBAP_PSE_UUID,
    MapClient,
    ObexClient,
    ObexError,
    PbapClient,
    StreamClosedError,
)
from phonebridge.services import (
    PhoneBusyError,
    PhoneDisconnectedError,
    PhoneNotPairedError,
    PhoneRequestError,
    PhoneUnavailableError,
)

if TYPE_CHECKING:
    import uuid
    from collections.abc import Awaitable
    from datetime import tzinfo

    from winrt.windows.devices.bluetooth.rfcomm import RfcommDeviceService

    from phonebridge.protocol import ListedMessage, MapEvent

logger = logging.getLogger(__name__)

# WSAEADDRINUSE as an HRESULT: another app on this PC holds the phone's service.
_HRESULT_ADDRESS_IN_USE = -2147014848
_PROTECTION = SocketProtectionLevel.BLUETOOTH_ENCRYPTION_ALLOW_NULL_AUTHENTICATION
_CLOSE_TIMEOUT = 3.0

BUSY = "Microsoft Phone Link is using the connection to your iPhone. Close Phone Link to use Phonebridge."
UNREACHABLE = (
    "Can't reach your iPhone. Keep it nearby and unlocked with Bluetooth on. Retrying automatically."
)
NOT_SHARING = (
    "Your iPhone isn't sharing messages with this PC. On the iPhone open Settings > Bluetooth, "
    "tap the (i) next to this PC and turn on Show Notifications."
)
NOT_PAIRED = "No paired iPhone found. Pair it in Windows Settings > Bluetooth & devices."
LOST = "Lost the connection to your iPhone."


def _device_name(device: BluetoothDevice) -> str:
    with contextlib.suppress(OSError):
        info_name = device.device_information.name
        if info_name:
            return info_name
    return device.name or "iPhone"


class WinRtPhoneConnector:
    def __init__(self, *, address: str | None, local_tz: tzinfo) -> None:
        self._address = int(address.replace(":", "").replace("-", ""), 16) if address else None
        self._tz = local_tz
        self._mns: MnsServer | None = None

    async def connect(self) -> WinRtPhoneSession:
        mns = await self._notification_server()
        device = await self._find_device()
        name = _device_name(device)
        service = await _find_service(device, MAS_UUID, missing=NOT_SHARING)
        socket = await _open_socket(service)
        stream = WinRtStream(socket)
        client = MapClient(ObexClient(stream), self._tz)
        try:
            await client.open()
        except (ObexError, StreamClosedError, TimeoutError) as exc:
            await stream.close()
            raise PhoneUnavailableError(UNREACHABLE) from exc
        session = WinRtPhoneSession(name=name, device=device, stream=stream, client=client, mns=mns)
        if mns is not None:
            mns.drain()
            try:
                await client.set_notifications(enabled=True)
            except ObexError as exc:
                logger.info("phone declined push notifications, polling instead: %s", exc)
            except (StreamClosedError, TimeoutError) as exc:
                await stream.close()
                raise PhoneDisconnectedError(LOST) from exc
        return session

    async def close(self) -> None:
        if self._mns is not None:
            await self._mns.stop()
            self._mns = None

    async def _notification_server(self) -> MnsServer | None:
        if self._mns is None:
            server = MnsServer(asyncio.get_running_loop())
            try:
                await server.start()
            except OSError as exc:
                logger.warning("could not publish the notification service, polling instead: %s", exc)
                return None
            self._mns = server
        return self._mns

    async def _find_device(self) -> BluetoothDevice:
        if self._address is not None:
            device = await BluetoothDevice.from_bluetooth_address_async(self._address)
            if device is None:
                raise PhoneNotPairedError(NOT_PAIRED)
            return device
        selector = BluetoothDevice.get_device_selector_from_pairing_state(True)
        for info in await DeviceInformation.find_all_async(selector, None):
            device = await BluetoothDevice.from_id_async(info.id)
            if device is None:
                continue
            result = await device.get_rfcomm_services_for_id_async(
                RfcommServiceId.from_uuid(MAS_UUID), BluetoothCacheMode.CACHED
            )
            if result.services:
                self._address = device.bluetooth_address
                logger.info("using paired phone %s", info.name)
                return device
        raise PhoneNotPairedError(NOT_PAIRED)


async def _find_service(
    device: BluetoothDevice, service_uuid: uuid.UUID, *, missing: str
) -> RfcommDeviceService:
    result = await device.get_rfcomm_services_for_id_async(
        RfcommServiceId.from_uuid(service_uuid), BluetoothCacheMode.UNCACHED
    )
    if result.services:
        return result.services[0]
    if result.error == BluetoothError.SUCCESS:
        raise PhoneUnavailableError(missing)
    raise PhoneUnavailableError(UNREACHABLE)


async def _open_socket(service: RfcommDeviceService) -> StreamSocket:
    socket = StreamSocket()
    try:
        await socket.connect_async(service.connection_host_name, service.connection_service_name, _PROTECTION)
    except OSError as exc:
        socket.close()
        if getattr(exc, "winerror", None) == _HRESULT_ADDRESS_IN_USE:
            raise PhoneBusyError(BUSY) from exc
        raise PhoneUnavailableError(UNREACHABLE) from exc
    return socket


class WinRtPhoneSession:
    def __init__(
        self,
        *,
        name: str,
        device: BluetoothDevice,
        stream: WinRtStream,
        client: MapClient,
        mns: MnsServer | None,
    ) -> None:
        self._name = name
        self._device = device
        self._stream = stream
        self._client = client
        self._mns = mns

    @property
    def phone_name(self) -> str:
        return self._name

    @property
    def push_enabled(self) -> bool:
        return self._mns is not None and self._mns.active

    async def list_inbox(self) -> list[ListedMessage]:
        return await self._call(self._client.list_messages("inbox"))

    async def fetch_text(self, handle: str) -> str:
        message = await self._call(self._client.get_message(handle))
        return message.text

    async def send(self, address: str, text: str) -> str | None:
        return await self._call(self._client.push_message(address, text))

    async def set_read(self, handle: str, *, read: bool) -> None:
        await self._call(self._client.set_read(handle, read=read))

    async def pull_contacts(self) -> list[Contact]:
        try:
            service = await _find_service(self._device, PBAP_PSE_UUID, missing="Contact sharing is off.")
            socket = await _open_socket(service)
        except (PhoneUnavailableError, PhoneBusyError) as exc:
            raise PhoneRequestError(str(exc)) from exc
        stream = WinRtStream(socket)
        try:
            cards = await PbapClient(ObexClient(stream, timeout=60.0)).pull_contacts()
        except (ObexError, StreamClosedError, TimeoutError) as exc:
            raise PhoneRequestError(f"Couldn't read contacts: {exc}") from exc
        finally:
            await stream.close()
        return [
            Contact(name=card.name or card.addresses[0], addresses=list(card.addresses)) for card in cards
        ]

    async def next_event(self) -> MapEvent:
        if self._mns is None:
            # No push channel: never resolves, so the caller's poll timeout drives refreshes.
            return await asyncio.get_running_loop().create_future()
        return await self._mns.next_event()

    async def close(self) -> None:
        with contextlib.suppress(ObexError, StreamClosedError, TimeoutError, OSError):
            async with asyncio.timeout(_CLOSE_TIMEOUT):
                await self._client.close()
        await self._stream.close()

    @staticmethod
    async def _call[T](operation: Awaitable[T]) -> T:
        try:
            return await operation
        except ObexError as exc:
            raise PhoneRequestError(str(exc)) from exc
        except (StreamClosedError, TimeoutError, OSError) as exc:
            raise PhoneDisconnectedError(LOST) from exc
