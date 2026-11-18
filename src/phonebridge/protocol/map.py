"""Bluetooth Message Access Profile (MAP) client and payload parsers."""

from __future__ import annotations

import logging
import re
import struct
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from enum import IntEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, field_validator

from phonebridge.protocol.bmessage import BMessage, build_bmessage, parse_bmessage
from phonebridge.protocol.obex import (
    Hdr,
    ObexClient,
    app_params_header,
    decode_unicode,
    type_header,
    unicode_header,
)

if TYPE_CHECKING:
    from phonebridge.protocol.obex import PutRequest

logger = logging.getLogger(__name__)

MAS_UUID = uuid.UUID("00001132-0000-1000-8000-00805f9b34fb")
MNS_UUID = uuid.UUID("00001133-0000-1000-8000-00805f9b34fb")
MAP_PROFILE_UUID16 = 0x1134
MAP_VERSION = 0x0104
MAS_TARGET = uuid.UUID("bb582b40-420c-11db-b0de-0800200c9a66").bytes
MNS_TARGET = uuid.UUID("bb582b41-420c-11db-b0de-0800200c9a66").bytes

# Features this app supports as a MAP client; published in our notification
# service record so the phone can pick its event and listing formats.
CLIENT_FEATURES = 0x1F | (1 << 6) | (1 << 9) | (1 << 18)

EVENT_REPORT_TYPE = "x-bt/MAP-event-report"
FILLER_BODY = b"0"
CHARSET_UTF8 = 1
MAX_SUBJECT_LENGTH = 255


class MapParam(IntEnum):
    MAX_LIST_COUNT = 0x01
    ATTACHMENT = 0x0A
    NOTIFICATION_STATUS = 0x0E
    SUBJECT_LENGTH = 0x13
    CHARSET = 0x14
    STATUS_INDICATOR = 0x17
    STATUS_VALUE = 0x18


_STATUS_READ = 0
_DATETIME = re.compile(r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(Z|[+-]\d{4})?")


def parse_map_datetime(value: str, local_tz: tzinfo) -> datetime:
    match = _DATETIME.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"unrecognised MAP timestamp: {value!r}")
    year, month, day, hour, minute, second, offset = match.groups()
    zone: tzinfo
    if offset is None:
        zone = local_tz
    elif offset == "Z":
        zone = UTC
    else:
        sign = 1 if offset[0] == "+" else -1
        zone = timezone(sign * timedelta(hours=int(offset[1:3]), minutes=int(offset[3:5])))
    return datetime(int(year), int(month), int(day), int(hour), int(minute), int(second), tzinfo=zone)


class ListedMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)

    handle: str
    subject: str = ""
    sent_at: datetime = Field(alias="datetime")
    sender_name: str = ""
    sender_addressing: str = ""
    recipient_name: str = ""
    recipient_addressing: str = ""
    msg_type: str = Field(default="", alias="type")
    size: int = 0
    attachment_size: int = 0
    read: bool = False
    sent: bool = False
    conversation_id: str | None = None
    conversation_name: str | None = None
    direction: str | None = None

    @field_validator("read", "sent", mode="before")
    @classmethod
    def _yes_no(cls, value: object) -> object:
        return value.strip().lower() == "yes" if isinstance(value, str) else value

    @field_validator("size", "attachment_size", mode="before")
    @classmethod
    def _blank_int(cls, value: object) -> object:
        return 0 if value == "" else value

    @field_validator("sent_at", mode="before")
    @classmethod
    def _map_datetime(cls, value: object, info: ValidationInfo) -> object:
        if not isinstance(value, str):
            return value
        context = info.context if isinstance(info.context, dict) else {}
        local_tz = context.get("tz")
        return parse_map_datetime(value, local_tz if isinstance(local_tz, tzinfo) else UTC)

    @property
    def has_full_text(self) -> bool:
        return bool(self.subject) and len(self.subject.encode("utf-8")) >= self.size


@dataclass(slots=True, frozen=True)
class MapEvent:
    kind: str
    handle: str
    folder: str
    msg_type: str


def parse_message_listing(xml: bytes, local_tz: tzinfo) -> list[ListedMessage]:
    if not xml.strip():
        return []
    out: list[ListedMessage] = []
    for element in ET.fromstring(xml).iter("msg"):
        try:
            out.append(ListedMessage.model_validate(dict(element.attrib), context={"tz": local_tz}))
        except ValidationError as exc:
            logger.warning("skipping malformed listing entry: %s", exc.errors(include_input=False))
    return out


def parse_event_report(xml: bytes) -> list[MapEvent]:
    if not xml.strip():
        return []
    return [
        MapEvent(
            kind=e.get("type", ""),
            handle=e.get("handle", ""),
            folder=e.get("folder", ""),
            msg_type=e.get("msg_type", ""),
        )
        for e in ET.fromstring(xml).iter("event")
    ]


def events_from_put(request: PutRequest) -> list[MapEvent]:
    if request.type != EVENT_REPORT_TYPE:
        return []
    return parse_event_report(request.body)


class MapClient:
    def __init__(self, obex: ObexClient, local_tz: tzinfo) -> None:
        self._obex = obex
        self._tz = local_tz

    async def open(self) -> None:
        await self._obex.connect(MAS_TARGET)
        await self._obex.setpath(None)
        await self._obex.setpath("telecom")
        await self._obex.setpath("msg")

    async def close(self) -> None:
        await self._obex.disconnect()

    async def list_messages(self, folder: str = "inbox", *, max_count: int = 1024) -> list[ListedMessage]:
        result = await self._obex.get(
            unicode_header(Hdr.NAME, folder)
            + type_header("x-bt/MAP-msg-listing")
            + app_params_header(
                {
                    MapParam.MAX_LIST_COUNT: struct.pack(">H", max_count),
                    MapParam.SUBJECT_LENGTH: bytes([MAX_SUBJECT_LENGTH]),
                }
            )
        )
        return parse_message_listing(result.body, self._tz)

    async def get_message(self, handle: str) -> BMessage:
        result = await self._obex.get(
            unicode_header(Hdr.NAME, handle)
            + type_header("x-bt/message")
            + app_params_header({MapParam.ATTACHMENT: b"\x00", MapParam.CHARSET: bytes([CHARSET_UTF8])})
        )
        return parse_bmessage(result.body.decode("utf-8", "replace"))

    async def push_message(self, recipient: str, text: str) -> str | None:
        response = await self._obex.put(
            unicode_header(Hdr.NAME, "outbox")
            + type_header("x-bt/message")
            + app_params_header({MapParam.CHARSET: bytes([CHARSET_UTF8])}),
            build_bmessage(recipient, text),
        )
        name = response.first_bytes(Hdr.NAME)
        return decode_unicode(name) if name else None

    async def set_read(self, handle: str, *, read: bool) -> None:
        await self._obex.put(
            unicode_header(Hdr.NAME, handle)
            + type_header("x-bt/messageStatus")
            + app_params_header(
                {MapParam.STATUS_INDICATOR: bytes([_STATUS_READ]), MapParam.STATUS_VALUE: bytes([int(read)])}
            ),
            FILLER_BODY,
        )

    async def set_notifications(self, *, enabled: bool) -> None:
        await self._obex.put(
            type_header("x-bt/MAP-NotificationRegistration")
            + app_params_header({MapParam.NOTIFICATION_STATUS: bytes([int(enabled)])}),
            FILLER_BODY,
        )
