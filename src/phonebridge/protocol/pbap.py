"""Bluetooth Phone Book Access Profile (PBAP) client: pulls the phone's contacts."""

from __future__ import annotations

import struct
import uuid
from enum import IntEnum
from typing import TYPE_CHECKING

from phonebridge.protocol.obex import Hdr, app_params_header, type_header, unicode_header
from phonebridge.protocol.vcard import VCard, parse_vcards

if TYPE_CHECKING:
    from phonebridge.protocol.obex import ObexClient

PBAP_PSE_UUID = uuid.UUID("0000112f-0000-1000-8000-00805f9b34fb")
PBAP_TARGET = uuid.UUID("796135f0-f0c5-11d8-0966-0800200c9a66").bytes


class PbapParam(IntEnum):
    MAX_LIST_COUNT = 0x04
    PROPERTY_SELECTOR = 0x06
    FORMAT = 0x07


_FORMAT_VCARD_30 = 1
# vCard properties: VERSION, FN, N, TEL, EMAIL.
_PROPERTIES = (1 << 0) | (1 << 1) | (1 << 2) | (1 << 7) | (1 << 8)
_ALL_ENTRIES = 0xFFFF


class PbapClient:
    def __init__(self, obex: ObexClient) -> None:
        self._obex = obex

    async def pull_contacts(self) -> list[VCard]:
        await self._obex.connect(PBAP_TARGET)
        try:
            result = await self._obex.get(
                unicode_header(Hdr.NAME, "telecom/pb.vcf")
                + type_header("x-bt/phonebook")
                + app_params_header(
                    {
                        PbapParam.FORMAT: bytes([_FORMAT_VCARD_30]),
                        PbapParam.MAX_LIST_COUNT: struct.pack(">H", _ALL_ENTRIES),
                        PbapParam.PROPERTY_SELECTOR: struct.pack(">Q", _PROPERTIES),
                    }
                )
            )
        finally:
            await self._obex.disconnect()
        return [card for card in parse_vcards(result.body.decode("utf-8", "replace")) if card.addresses]
