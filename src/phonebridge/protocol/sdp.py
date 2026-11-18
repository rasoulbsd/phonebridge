"""Bluetooth SDP data elements: just enough to read and publish service records."""

from __future__ import annotations

import struct

type SdpValue = int | str | bool | list[SdpValue] | None

_TYPE_NIL = 0
_TYPE_UINT = 1
_TYPE_INT = 2
_TYPE_UUID = 3
_TYPE_TEXT = 4
_TYPE_BOOL = 5
_TYPE_SEQUENCE = 6
_TYPE_ALTERNATIVE = 7
_TYPE_URL = 8
_FIXED_SIZES = (1, 2, 4, 8, 16)
_LENGTH_PREFIX_SIZES = (1, 2, 4)
_MAX_FIXED_INDEX = 4


def parse_element(data: bytes, offset: int = 0) -> tuple[SdpValue, int]:
    descriptor = data[offset]
    kind, size_index = descriptor >> 3, descriptor & 0x07
    i = offset + 1
    if kind == _TYPE_NIL:
        return None, i
    if size_index <= _MAX_FIXED_INDEX:
        length = _FIXED_SIZES[size_index]
    else:
        prefix = _LENGTH_PREFIX_SIZES[size_index - 5]
        length = int.from_bytes(data[i : i + prefix], "big")
        i += prefix
    raw = data[i : i + length]
    end = i + length
    match kind:
        case 1 | 2:
            return int.from_bytes(raw, "big", signed=kind == _TYPE_INT), end
        case 5:
            return bool(raw[0]), end
        case 3:
            return raw.hex(), end
        case 4 | 8:
            return raw.decode("utf-8", "replace").rstrip("\0"), end
        case 6 | 7:
            items: list[SdpValue] = []
            j = 0
            while j < len(raw):
                value, j = parse_element(raw, j)
                items.append(value)
            return items, end
        case _:
            return raw.hex(), end


def uint8(value: int) -> bytes:
    return bytes([(_TYPE_UINT << 3) | 0, value])


def uint16(value: int) -> bytes:
    return bytes([(_TYPE_UINT << 3) | 1]) + struct.pack(">H", value)


def uint32(value: int) -> bytes:
    return bytes([(_TYPE_UINT << 3) | 2]) + struct.pack(">I", value)


def uuid16(value: int) -> bytes:
    return bytes([(_TYPE_UUID << 3) | 1]) + struct.pack(">H", value)


def text(value: str) -> bytes:
    data = value.encode("utf-8")
    return bytes([(_TYPE_TEXT << 3) | 5, len(data)]) + data


def sequence(*elements: bytes) -> bytes:
    data = b"".join(elements)
    return bytes([(_TYPE_SEQUENCE << 3) | 5, len(data)]) + data
