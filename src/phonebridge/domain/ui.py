"""Types the UI sees (docs/ui-api.md). Both the Mac and the Bluetooth backends produce these."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Mode(StrEnum):
    MAC = "mac"
    PHONE = "phone"
    DEMO = "demo"


class State(StrEnum):
    NOT_CONFIGURED = "not_configured"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    BUSY = "busy"
    NO_PHONE = "no_phone"
    AUTH_FAILED = "auth_failed"


class Delivery(StrEnum):
    SENDING = "sending"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"
    RECEIVED = "received"


class ReactionType(StrEnum):
    LOVE = "love"
    LIKE = "like"
    DISLIKE = "dislike"
    LAUGH = "laugh"
    EMPHASIZE = "emphasize"
    QUESTION = "question"


class CallStatus(StrEnum):
    INCOMING = "incoming"
    RINGING = "ringing"
    # A call this PC placed through the Mac: their phone is ringing.
    OUTGOING = "outgoing"
    # They answered; the Mac is preparing to hand the call to this PC.
    ANSWERED = "answered"
    # The PC should open the call now (POST /api/calls/{id}/join).
    READY = "ready"
    CONNECTED = "connected"
    ENDED = "ended"


class Person(_Frozen):
    address: str
    name: str | None = None
    photo: str | None = None


class Features(_Frozen):
    groups: bool = False
    attachments: bool = False
    send_attachments: bool = False
    reactions: bool = False
    replies: bool = False
    edit: bool = False
    unsend: bool = False
    typing: bool = False
    read_receipts: bool = False
    manage_groups: bool = False
    new_group: bool = False
    facetime: bool = False
    direct_calls: bool = False
    search: bool = True
    contacts: bool = True


class MacInfo(_Frozen):
    url: str
    server_version: str | None = None
    macos_version: str | None = None
    private_api: bool = False


class AppStatus(_Frozen):
    mode: Mode
    state: State
    device_name: str | None = None
    detail: str | None = None
    features: Features = Features()
    notifications: Literal["push", "polling"] | None = None
    last_sync_at: datetime | None = None
    contacts_count: int = 0
    mac: MacInfo | None = None


class Chat(_Frozen):
    id: str
    title: str
    is_group: bool = False
    service: Literal["iMessage", "SMS", "RCS"] | None = None
    participants: list[Person] = []
    has_custom_name: bool = False
    last_text: str = ""
    last_at: datetime | None = None
    last_from_me: bool = False
    last_sender: Person | None = None
    unread: int = 0


class Attachment(_Frozen):
    id: str
    name: str
    mime: str
    size: int = 0
    width: int | None = None
    height: int | None = None
    url: str
    thumb_url: str | None = None
    available: bool = True


class Reaction(_Frozen):
    type: ReactionType
    from_me: bool
    sender: Person | None = None


class ReplyRef(_Frozen):
    id: str
    text: str
    sender_name: str | None = None


class ChatMessage(_Frozen):
    id: str
    chat_id: str
    temp_id: str | None = None
    from_me: bool
    sender: Person | None = None
    text: str = ""
    at: datetime
    status: Delivery
    delivered_at: datetime | None = None
    read_at: datetime | None = None
    error: str | None = None
    attachments: list[Attachment] = []
    reactions: list[Reaction] = []
    reply_to: ReplyRef | None = None
    edited: bool = False
    unsent: bool = False
    event: str | None = None


class ThreadPage(_Frozen):
    chat: Chat
    messages: list[ChatMessage]
    cursor: str | None = None


class ChatSearchHit(_Frozen):
    message: ChatMessage
    chat: Chat


class ContactCard(_Frozen):
    name: str
    addresses: list[str]


class Call(_Frozen):
    id: str
    status: CallStatus
    caller: Person | None = None
    is_video: bool = True
    outgoing: bool = False
    detail: str | None = None


class StatusUpdate(_Frozen):
    type: Literal["status"] = "status"
    status: AppStatus


class ChatUpdate(_Frozen):
    type: Literal["chat"] = "chat"
    chat: Chat


class ChatRemoved(_Frozen):
    type: Literal["chat_removed"] = "chat_removed"
    chat_id: str


class MessageUpdate(_Frozen):
    type: Literal["message"] = "message"
    message: ChatMessage
    chat: Chat


class TypingUpdate(_Frozen):
    type: Literal["typing"] = "typing"
    chat_id: str
    typing: bool
    person: Person | None = None


class CallUpdate(_Frozen):
    type: Literal["call"] = "call"
    call: Call


type UiUpdate = StatusUpdate | ChatUpdate | ChatRemoved | MessageUpdate | TypingUpdate | CallUpdate
