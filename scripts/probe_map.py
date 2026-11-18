"""Probe what a paired iPhone exposes over Bluetooth MAP.

Prints only counts, field names and feature flags, never message text,
names or phone numbers.
Usage: .venv\\Scripts\\python.exe probe_map.py <phone Bluetooth address, e.g. AA:BB:CC:DD:EE:FF>
"""

from __future__ import annotations

import asyncio
import struct
import sys
import uuid
import xml.etree.ElementTree as ET
from collections import Counter

from winrt.windows.devices.bluetooth import BluetoothCacheMode, BluetoothDevice
from winrt.windows.networking.sockets import SocketProtectionLevel, StreamSocket
from winrt.windows.storage.streams import DataReader, DataWriter, InputStreamOptions

MAS_UUID = uuid.UUID("00001132-0000-1000-8000-00805f9b34fb")
MAP_TARGET = uuid.UUID("bb582b40-420c-11db-b0de-0800200c9a66").bytes

MAP_FEATURE_BITS = [
    "notification registration",
    "notification",
    "browsing",
    "uploading (send)",
    "delete",
    "instance information",
    "extended event report 1.1",
    "event report 1.2",
    "message format 1.1",
    "messages-listing format 1.1",
    "persistent message handles",
    "database identifier",
    "folder version counter",
    "conversation version counters",
    "participant presence notification",
    "participant chat state notification",
    "PBAP contact cross reference",
    "notification filtering",
    "UTC offset timestamps",
    "MapSupportedFeatures in Connect",
    "conversation listing",
    "owner status",
    "message forwarding",
]
MSG_TYPE_BITS = ["EMAIL", "SMS_GSM", "SMS_CDMA", "MMS", "IM"]

# Features this client claims in CONNECT, so the phone unlocks 1.1 listings
# (conversation ids) and conversation listing if it supports them.
CLIENT_FEATURES = 0x003FF | (1 << 20)

HI_NAME = 0x01
HI_TYPE = 0x42
HI_TARGET = 0x46
HI_BODY = 0x48
HI_END_OF_BODY = 0x49
HI_APP_PARAMS = 0x4C
HI_CONNECTION_ID = 0xCB

OP_CONNECT = 0x80
OP_DISCONNECT = 0x81
OP_GET_FINAL = 0x83
OP_SETPATH = 0x85
RSP_CONTINUE = 0x90
RSP_SUCCESS = 0xA0


def parse_sdp_element(data: bytes, i: int = 0) -> tuple[object, int]:
    desc = data[i]
    kind, size_idx = desc >> 3, desc & 0x07
    i += 1
    if kind == 0:
        return None, i
    if size_idx <= 4:
        length = [1, 2, 4, 8, 16][size_idx]
    else:
        n = [1, 2, 4][size_idx - 5]
        length = int.from_bytes(data[i : i + n], "big")
        i += n
    raw = data[i : i + length]
    end = i + length
    match kind:
        case 1 | 2 | 5:
            return int.from_bytes(raw, "big", signed=kind == 2), end
        case 3:
            return raw.hex(), end
        case 4 | 8:
            return raw.decode("utf-8", "replace").rstrip("\0"), end
        case 6 | 7:
            items: list[object] = []
            j = 0
            while j < len(raw):
                value, j = parse_sdp_element(raw, j)
                items.append(value)
            return items, end
        case _:
            return raw.hex(), end


def h_unicode(hi: int, text: str | None) -> bytes:
    data = (text + "\0").encode("utf-16-be") if text else b""
    return bytes([hi]) + struct.pack(">H", 3 + len(data)) + data


def h_bytes(hi: int, data: bytes) -> bytes:
    return bytes([hi]) + struct.pack(">H", 3 + len(data)) + data


def h_u32(hi: int, value: int) -> bytes:
    return bytes([hi]) + struct.pack(">I", value)


def app_params(*tlvs: tuple[int, bytes]) -> bytes:
    return h_bytes(HI_APP_PARAMS, b"".join(bytes([t, len(v)]) + v for t, v in tlvs))


def parse_headers(data: bytes) -> list[tuple[int, bytes | int]]:
    out: list[tuple[int, bytes | int]] = []
    i = 0
    while i < len(data):
        hi = data[i]
        match hi & 0xC0:
            case 0x00 | 0x40:
                length = struct.unpack(">H", data[i + 1 : i + 3])[0]
                out.append((hi, data[i + 3 : i + length]))
                i += length
            case 0x80:
                out.append((hi, data[i + 1]))
                i += 2
            case _:
                out.append((hi, struct.unpack(">I", data[i + 1 : i + 5])[0]))
                i += 5
    return out


class ObexError(Exception):
    pass


class ObexClient:
    def __init__(self, sock: StreamSocket) -> None:
        self._reader = DataReader(sock.input_stream)
        self._reader.input_stream_options = InputStreamOptions.PARTIAL
        self._writer = DataWriter(sock.output_stream)
        self._conn_id: int | None = None

    async def _read_exact(self, n: int) -> bytes:
        out = bytearray()
        while len(out) < n:
            got = await self._reader.load_async(n - len(out))
            if got == 0:
                raise ConnectionError("phone closed the connection")
            out += bytes(self._reader.read_buffer(got))
        return bytes(out)

    async def _request(self, opcode: int, body: bytes, connect_rsp: bool = False) -> tuple[int, list[tuple[int, bytes | int]]]:
        packet = bytes([opcode]) + struct.pack(">H", 3 + len(body)) + body
        self._writer.write_bytes(packet)
        await self._writer.store_async()
        head = await asyncio.wait_for(self._read_exact(3), 20)
        code, length = head[0], struct.unpack(">H", head[1:3])[0]
        rest = await asyncio.wait_for(self._read_exact(length - 3), 20)
        if connect_rsp and code == RSP_SUCCESS:
            rest = rest[4:]
        return code, parse_headers(rest)

    def _cid(self) -> bytes:
        return h_u32(HI_CONNECTION_ID, self._conn_id) if self._conn_id is not None else b""

    async def connect(self, extra: bytes = b"") -> None:
        body = bytes([0x10, 0x00]) + struct.pack(">H", 0x4000) + h_bytes(HI_TARGET, MAP_TARGET) + extra
        code, headers = await self._request(OP_CONNECT, body, connect_rsp=True)
        if code != RSP_SUCCESS:
            raise ObexError(f"CONNECT refused: 0x{code:02X}")
        for hi, value in headers:
            if hi == HI_CONNECTION_ID and isinstance(value, int):
                self._conn_id = value

    async def setpath(self, name: str | None) -> None:
        body = bytes([0x02, 0x00]) + self._cid() + h_unicode(HI_NAME, name)
        code, _ = await self._request(OP_SETPATH, body)
        if code != RSP_SUCCESS:
            raise ObexError(f"SETPATH {name!r} refused: 0x{code:02X}")

    async def get(self, headers: bytes) -> tuple[bytes, bytes]:
        body = bytearray()
        params = b""
        req = self._cid() + headers
        while True:
            code, resp = await self._request(OP_GET_FINAL, req)
            for hi, value in resp:
                if hi in (HI_BODY, HI_END_OF_BODY) and isinstance(value, bytes):
                    body += value
                elif hi == HI_APP_PARAMS and isinstance(value, bytes):
                    params = value
            if code == RSP_CONTINUE:
                req = self._cid()
                continue
            if code == RSP_SUCCESS:
                return bytes(body), params
            raise ObexError(f"GET refused: 0x{code:02X}")

    async def disconnect(self) -> None:
        await self._request(OP_DISCONNECT, self._cid())


def parse_tlvs(data: bytes) -> dict[int, bytes]:
    out: dict[int, bytes] = {}
    i = 0
    while i + 2 <= len(data):
        tag, length = data[i], data[i + 1]
        out[tag] = data[i + 2 : i + 2 + length]
        i += 2 + length
    return out


def summarize_listing(xml_bytes: bytes) -> None:
    root = ET.fromstring(xml_bytes)
    msgs = root.findall("msg")
    print(f"    listing version: {root.get('version')}, messages returned: {len(msgs)}")
    print(f"    types: {dict(Counter(m.get('type') for m in msgs))}")
    attrs = sorted({a for m in msgs for a in m.attrib})
    print(f"    fields per message: {attrs}")
    convo_ids = {m.get("conversation_id") for m in msgs if m.get("conversation_id")}
    senders = {m.get("sender_addressing") for m in msgs if m.get("sender_addressing")}
    print(f"    distinct conversation ids: {len(convo_ids)}, distinct senders: {len(senders)}")
    multi = sum(1 for m in msgs if ";" in (m.get("recipient_addressing") or ""))
    print(f"    messages with several recipients: {multi}")
    if msgs:
        dates = sorted(m.get("datetime") or "" for m in msgs)
        print(f"    oldest/newest timestamp: {dates[0][:8]} / {dates[-1][:8]}")


def summarize_convos(xml_bytes: bytes) -> None:
    root = ET.fromstring(xml_bytes)
    convos = root.findall("conversation")
    sizes = Counter(len(c.findall("participant")) for c in convos)
    print(f"    conversations: {len(convos)}, participants per conversation: {dict(sorted(sizes.items()))}")
    print(f"    conversation fields: {sorted({a for c in convos for a in c.attrib})}")


async def main(address: str) -> None:
    device = await BluetoothDevice.from_bluetooth_address_async(int(address.replace(":", ""), 16))
    if device is None:
        sys.exit("Phone not found. Is it paired with this PC?")
    print(f"Device: {device.name}  status: {device.connection_status}")

    result = await device.get_rfcomm_services_with_cache_mode_async(BluetoothCacheMode.UNCACHED)
    print(f"RFCOMM services on phone: {len(result.services)} (error: {result.error})")
    mas = None
    mas_features = 0
    for svc in result.services:
        attrs = await svc.get_sdp_raw_attributes_with_cache_mode_async(BluetoothCacheMode.UNCACHED)
        parsed = {k: parse_sdp_element(bytes(v))[0] for k, v in attrs.items()}
        name = parsed.get(0x0100, "?")
        sid = uuid.UUID(str(svc.service_id.uuid))
        print(f"  - {sid}  {name}")
        if sid == MAS_UUID and mas is None:
            mas = svc
            mas_features = int(parsed.get(0x0317, 0x1F) or 0)
            types = int(parsed.get(0x0316, 0) or 0)
            print(f"      MAS instance: {parsed.get(0x0315)}  version attr: {parsed.get(0x0009)}")
            print(f"      message types: {[t for b, t in enumerate(MSG_TYPE_BITS) if types >> b & 1]}")
            print(f"      features 0x{mas_features:08X}:")
            for b, label in enumerate(MAP_FEATURE_BITS):
                print(f"        [{'x' if mas_features >> b & 1 else ' '}] {label}")
    if mas is None:
        sys.exit("No MAP service found. On the iPhone: Settings > Bluetooth > (i) next to this PC > turn on Show Notifications.")

    sock = StreamSocket()
    print("Connecting to MAP service...")
    await sock.connect_with_protection_level_async(
        mas.connection_host_name,
        mas.connection_service_name,
        SocketProtectionLevel.BLUETOOTH_ENCRYPTION_ALLOW_NULL_AUTHENTICATION,
    )
    obex = ObexClient(sock)
    extra = app_params((0x29, struct.pack(">I", CLIENT_FEATURES))) if mas_features >> 19 & 1 else b""
    await obex.connect(extra)
    print("  OBEX session open")

    await obex.setpath(None)
    await obex.setpath("telecom")
    await obex.setpath("msg")
    body, _ = await obex.get(h_bytes(HI_TYPE, b"x-bt/folder-listing\0"))
    print(f"  folder listing bytes: {len(body)}")
    folders = [f.get("name") for f in ET.fromstring(body).findall("folder")] if body.strip() else []
    print(f"  folders: {folders or '(none listed, trying defaults)'}")
    folders = folders or ["inbox", "sent", "outbox", "deleted"]

    for folder in ("inbox", "sent"):
        if folder not in folders:
            continue
        print(f"  [{folder}]")
        try:
            _, params = await obex.get(
                h_unicode(HI_NAME, folder)
                + h_bytes(HI_TYPE, b"x-bt/MAP-msg-listing\0")
                + app_params((0x01, struct.pack(">H", 0)))
            )
            size = parse_tlvs(params).get(0x12)
            print(f"    total messages reported: {int.from_bytes(size, 'big') if size else 'n/a'}")
            body, _ = await obex.get(
                h_unicode(HI_NAME, folder)
                + h_bytes(HI_TYPE, b"x-bt/MAP-msg-listing\0")
                + app_params((0x01, struct.pack(">H", 200)), (0x13, bytes([0])))
            )
            summarize_listing(body)
        except (ObexError, ET.ParseError) as exc:
            print(f"    failed: {exc}")

    if mas_features >> 20 & 1:
        print("  [conversation listing]")
        try:
            body, _ = await obex.get(
                h_bytes(HI_TYPE, b"x-bt/MAP-convo-listing\0") + app_params((0x01, struct.pack(">H", 200)))
            )
            summarize_convos(body)
        except (ObexError, ET.ParseError) as exc:
            print(f"    failed: {exc}")

    await obex.disconnect()
    sock.close()
    print("Done.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: probe_map.py <phone Bluetooth address, e.g. AA:BB:CC:DD:EE:FF>")
    asyncio.run(main(sys.argv[1]))
