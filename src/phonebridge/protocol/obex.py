"""OBEX client and server sessions over a byte stream (Bluetooth RFCOMM in practice)."""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping


class ByteStream(Protocol):
    async def read_exact(self, size: int) -> bytes: ...

    async def write(self, data: bytes) -> None: ...

    async def close(self) -> None: ...


class StreamClosedError(ConnectionError):
    pass


class ObexError(Exception):
    def __init__(self, operation: str, code: int) -> None:
        super().__init__(f"{operation} refused by the phone (OBEX 0x{code:02X})")
        self.operation = operation
        self.code = code


class Op(IntEnum):
    PUT = 0x02
    CONNECT = 0x80
    DISCONNECT = 0x81
    PUT_FINAL = 0x82
    GET_FINAL = 0x83
    SETPATH = 0x85
    ABORT = 0xFF


class Rsp(IntEnum):
    CONTINUE = 0x90
    SUCCESS = 0xA0
    BAD_REQUEST = 0xC0
    NOT_ACCEPTABLE = 0xC6
    NOT_IMPLEMENTED = 0xD1


class Hdr(IntEnum):
    NAME = 0x01
    TYPE = 0x42
    TARGET = 0x46
    BODY = 0x48
    END_OF_BODY = 0x49
    WHO = 0x4A
    APP_PARAMS = 0x4C
    CONNECTION_ID = 0xCB


OBEX_VERSION = 0x10
DEFAULT_MAX_PACKET = 0x4000
MIN_MAX_PACKET = 255
SETPATH_DONT_CREATE = 0x02
_PACKET_HEAD = 3
_CONNECT_FIELDS = 4


@dataclass(slots=True, frozen=True)
class Header:
    id: int
    value: bytes | int


@dataclass(slots=True, frozen=True)
class Packet:
    code: int
    headers: list[Header]

    def first(self, header_id: int) -> bytes | int | None:
        return next((h.value for h in self.headers if h.id == header_id), None)

    def first_bytes(self, header_id: int) -> bytes | None:
        value = self.first(header_id)
        return value if isinstance(value, bytes) else None

    @property
    def body(self) -> bytes:
        return b"".join(
            h.value
            for h in self.headers
            if h.id in (Hdr.BODY, Hdr.END_OF_BODY) and isinstance(h.value, bytes)
        )


@dataclass(slots=True, frozen=True)
class GetResult:
    body: bytes
    app_params: dict[int, bytes]


@dataclass(slots=True, frozen=True)
class PutRequest:
    type: str
    name: str | None
    app_params: dict[int, bytes]
    body: bytes


def unicode_header(header_id: int, text: str | None) -> bytes:
    data = (text + "\0").encode("utf-16-be") if text else b""
    return bytes([header_id]) + struct.pack(">H", 3 + len(data)) + data


def bytes_header(header_id: int, data: bytes) -> bytes:
    return bytes([header_id]) + struct.pack(">H", 3 + len(data)) + data


def u32_header(header_id: int, value: int) -> bytes:
    return bytes([header_id]) + struct.pack(">I", value)


def type_header(mime: str) -> bytes:
    return bytes_header(Hdr.TYPE, mime.encode("ascii") + b"\0")


def encode_tlvs(params: Mapping[int, bytes]) -> bytes:
    return b"".join(bytes([tag, len(value)]) + value for tag, value in params.items())


def app_params_header(params: Mapping[int, bytes]) -> bytes:
    return bytes_header(Hdr.APP_PARAMS, encode_tlvs(params))


def parse_tlvs(data: bytes) -> dict[int, bytes]:
    out: dict[int, bytes] = {}
    i = 0
    while i + 2 <= len(data):
        tag, length = data[i], data[i + 1]
        out[tag] = data[i + 2 : i + 2 + length]
        i += 2 + length
    return out


def decode_unicode(value: bytes) -> str:
    return value.decode("utf-16-be", "replace").rstrip("\0")


def decode_type(value: bytes) -> str:
    return value.decode("ascii", "replace").rstrip("\0")


def parse_headers(data: bytes) -> list[Header]:
    out: list[Header] = []
    i = 0
    while i < len(data):
        header_id = data[i]
        match header_id & 0xC0:
            case 0x00 | 0x40:
                length = struct.unpack(">H", data[i + 1 : i + 3])[0]
                if length < _PACKET_HEAD:
                    raise ValueError("malformed OBEX header length")
                out.append(Header(header_id, data[i + 3 : i + length]))
                i += length
            case 0x80:
                out.append(Header(header_id, data[i + 1]))
                i += 2
            case _:
                out.append(Header(header_id, struct.unpack(">I", data[i + 1 : i + 5])[0]))
                i += 5
    return out


def encode_packet(code: int, payload: bytes) -> bytes:
    return bytes([code]) + struct.pack(">H", _PACKET_HEAD + len(payload)) + payload


async def read_packet(stream: ByteStream) -> tuple[int, bytes]:
    head = await stream.read_exact(_PACKET_HEAD)
    length = struct.unpack(">H", head[1:3])[0]
    if length < _PACKET_HEAD:
        raise ValueError("malformed OBEX packet length")
    rest = await stream.read_exact(length - _PACKET_HEAD) if length > _PACKET_HEAD else b""
    return head[0], rest


class ObexClient:
    """One OBEX session. Every operation holds the lock, because OBEX allows only
    one request in flight per connection."""

    def __init__(self, stream: ByteStream, *, timeout: float = 20.0) -> None:
        self._stream = stream
        self._timeout = timeout
        self._conn_id: int | None = None
        self._max_packet = MIN_MAX_PACKET
        self._lock = asyncio.Lock()

    @property
    def max_packet(self) -> int:
        return self._max_packet

    def _cid(self) -> bytes:
        return u32_header(Hdr.CONNECTION_ID, self._conn_id) if self._conn_id is not None else b""

    async def _exchange(self, code: int, payload: bytes, *, connect: bool = False) -> tuple[Packet, bytes]:
        async with asyncio.timeout(self._timeout):
            await self._stream.write(encode_packet(code, payload))
            rsp_code, rest = await read_packet(self._stream)
        prefix = b""
        if connect and rsp_code == Rsp.SUCCESS:
            prefix, rest = rest[:_CONNECT_FIELDS], rest[_CONNECT_FIELDS:]
        return Packet(rsp_code, parse_headers(rest)), prefix

    async def connect(self, target: bytes, extra_headers: bytes = b"") -> None:
        payload = (
            bytes([OBEX_VERSION, 0x00])
            + struct.pack(">H", DEFAULT_MAX_PACKET)
            + bytes_header(Hdr.TARGET, target)
            + extra_headers
        )
        async with self._lock:
            rsp, prefix = await self._exchange(Op.CONNECT, payload, connect=True)
        if rsp.code != Rsp.SUCCESS:
            raise ObexError("CONNECT", rsp.code)
        if len(prefix) == _CONNECT_FIELDS:
            server_max = struct.unpack(">H", prefix[2:4])[0]
            self._max_packet = max(MIN_MAX_PACKET, min(DEFAULT_MAX_PACKET, server_max))
        conn_id = rsp.first(Hdr.CONNECTION_ID)
        self._conn_id = conn_id if isinstance(conn_id, int) else None

    async def disconnect(self) -> None:
        async with self._lock:
            await self._exchange(Op.DISCONNECT, self._cid())

    async def setpath(self, name: str | None) -> None:
        """Enter child folder `name`, or go to the root when `name` is None."""
        payload = bytes([SETPATH_DONT_CREATE, 0x00]) + self._cid() + unicode_header(Hdr.NAME, name)
        async with self._lock:
            rsp, _ = await self._exchange(Op.SETPATH, payload)
        if rsp.code != Rsp.SUCCESS:
            raise ObexError(f"SETPATH {name or '/'}", rsp.code)

    async def get(self, headers: bytes) -> GetResult:
        body = bytearray()
        params: dict[int, bytes] = {}
        payload = self._cid() + headers
        async with self._lock:
            while True:
                rsp, _ = await self._exchange(Op.GET_FINAL, payload)
                body += rsp.body
                raw_params = rsp.first_bytes(Hdr.APP_PARAMS)
                if raw_params:
                    params.update(parse_tlvs(raw_params))
                if rsp.code == Rsp.CONTINUE:
                    payload = self._cid()
                    continue
                if rsp.code == Rsp.SUCCESS:
                    return GetResult(bytes(body), params)
                raise ObexError("GET", rsp.code)

    async def put(self, headers: bytes, body: bytes) -> Packet:
        prefix = self._cid() + headers
        remaining = body
        async with self._lock:
            while True:
                room = self._max_packet - _PACKET_HEAD - len(prefix) - _PACKET_HEAD
                if room <= 0:
                    raise ValueError("OBEX headers do not fit in one packet")
                chunk, remaining = remaining[:room], remaining[room:]
                final = not remaining
                payload = prefix + bytes_header(Hdr.END_OF_BODY if final else Hdr.BODY, chunk)
                rsp, _ = await self._exchange(Op.PUT_FINAL if final else Op.PUT, payload)
                if final:
                    if rsp.code != Rsp.SUCCESS:
                        raise ObexError("PUT", rsp.code)
                    return rsp
                if rsp.code != Rsp.CONTINUE:
                    raise ObexError("PUT", rsp.code)
                prefix = self._cid()


class ObexServer:
    """Answers one incoming OBEX connection, e.g. the phone's notification channel."""

    def __init__(self, stream: ByteStream, *, target: bytes, connection_id: int = 1) -> None:
        self._stream = stream
        self._target = target
        self._connection_id = connection_id

    async def _respond(self, code: int, payload: bytes = b"") -> None:
        await self._stream.write(encode_packet(code, payload))

    async def serve(self, on_put: Callable[[PutRequest], Awaitable[None]]) -> None:
        headers: list[Header] = []
        try:
            while True:
                code, rest = await read_packet(self._stream)
                match code:
                    case Op.CONNECT:
                        target = Packet(code, parse_headers(rest[_CONNECT_FIELDS:])).first(Hdr.TARGET)
                        if target != self._target:
                            await self._respond(Rsp.NOT_ACCEPTABLE)
                            continue
                        await self._respond(
                            Rsp.SUCCESS,
                            bytes([OBEX_VERSION, 0x00])
                            + struct.pack(">H", DEFAULT_MAX_PACKET)
                            + u32_header(Hdr.CONNECTION_ID, self._connection_id)
                            + bytes_header(Hdr.WHO, self._target),
                        )
                    case Op.PUT:
                        headers += parse_headers(rest)
                        await self._respond(Rsp.CONTINUE)
                    case Op.PUT_FINAL:
                        headers += parse_headers(rest)
                        await self._respond(Rsp.SUCCESS)
                        await on_put(_to_put_request(headers))
                        headers = []
                    case Op.ABORT:
                        headers = []
                        await self._respond(Rsp.SUCCESS)
                    case Op.DISCONNECT:
                        await self._respond(Rsp.SUCCESS)
                        return
                    case _:
                        await self._respond(Rsp.NOT_IMPLEMENTED)
        except StreamClosedError:
            return


def _to_put_request(headers: list[Header]) -> PutRequest:
    packet = Packet(Rsp.SUCCESS, headers)
    type_value = packet.first_bytes(Hdr.TYPE)
    name_value = packet.first_bytes(Hdr.NAME)
    params: dict[int, bytes] = {}
    for header in headers:
        if header.id == Hdr.APP_PARAMS and isinstance(header.value, bytes):
            params.update(parse_tlvs(header.value))
    return PutRequest(
        type=decode_type(type_value) if type_value else "",
        name=decode_unicode(name_value) if name_value else None,
        app_params=params,
        body=packet.body,
    )
