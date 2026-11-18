from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Direction(StrEnum):
    IN = "in"
    OUT = "out"


class MessageStatus(StrEnum):
    RECEIVED = "received"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"


class ConnectionState(StrEnum):
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    BUSY = "busy"
    NO_PHONE = "no_phone"


class NotificationMode(StrEnum):
    PUSH = "push"
    POLLING = "polling"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Contact(_Frozen):
    name: str
    addresses: list[str]


class NewMessage(_Frozen):
    dedupe_key: str
    conversation_id: str
    direction: Direction
    address: str
    name: str | None
    text: str
    at: datetime
    read: bool
    status: MessageStatus
    error: str | None = None
    has_attachment: bool = False
    phone_handle: str | None = None
    session_id: str | None = None


class Message(_Frozen):
    id: int
    conversation_id: str
    direction: Direction
    address: str
    text: str
    at: datetime
    read: bool
    status: MessageStatus
    error: str | None = None
    has_attachment: bool = False
    # Phone handles are only valid inside the Bluetooth session that produced them.
    phone_handle: str | None = Field(default=None, exclude=True)
    session_id: str | None = Field(default=None, exclude=True)


class Conversation(_Frozen):
    id: str
    address: str
    name: str | None
    last_text: str
    last_at: datetime
    last_direction: Direction
    unread: int


class SearchHit(_Frozen):
    message: Message
    conversation: Conversation


class PhoneStatus(_Frozen):
    state: ConnectionState
    phone_name: str | None = None
    detail: str | None = None
    notifications: NotificationMode | None = None
    last_sync_at: datetime | None = None
    contacts_count: int = 0


class StatusEvent(_Frozen):
    type: Literal["status"] = "status"
    status: PhoneStatus


class MessageEvent(_Frozen):
    type: Literal["message"] = "message"
    message: Message
    conversation: Conversation


class ConversationEvent(_Frozen):
    type: Literal["conversation"] = "conversation"
    conversation: Conversation


type UiEvent = StatusEvent | MessageEvent | ConversationEvent
