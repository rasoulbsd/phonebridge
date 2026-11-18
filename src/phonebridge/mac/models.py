"""BlueBubbles Server payloads (REST v1 and Socket.IO), validated at the boundary."""

from __future__ import annotations

import base64
import binascii

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class _BB(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore", frozen=True)


class BBHandle(_BB):
    address: str
    service: str | None = None
    original_rowid: int | None = Field(default=None, alias="originalROWID")


class BBAttachment(_BB):
    guid: str
    mime_type: str | None = None
    transfer_name: str | None = None
    total_bytes: int = 0
    height: int | None = None
    width: int | None = None
    is_sticker: bool = False
    hide_attachment: bool = False
    has_live_photo: bool = False


class BBChatRef(_BB):
    guid: str
    display_name: str | None = None
    style: int | None = None
    chat_identifier: str | None = None
    participants: list[BBHandle] = []


class BBMessage(_BB):
    guid: str
    text: str | None = None
    subject: str | None = None
    handle: BBHandle | None = None
    is_from_me: bool = False
    date_created: int = 0
    date_read: int | None = None
    date_delivered: int | None = None
    is_delivered: bool = False
    error: int = 0
    attachments: list[BBAttachment] = []
    chats: list[BBChatRef] = []
    associated_message_guid: str | None = None
    associated_message_type: str | None = None
    thread_originator_guid: str | None = None
    item_type: int = 0
    group_action_type: int = 0
    group_title: str | None = None
    other_handle: int | None = None
    date_edited: int | None = None
    date_retracted: int | None = None
    balloon_bundle_id: str | None = None
    temp_guid: str | None = None


class BBChat(_BB):
    guid: str
    display_name: str | None = None
    style: int | None = None
    chat_identifier: str | None = None
    is_archived: bool = False
    participants: list[BBHandle] = []
    last_message: BBMessage | None = None


class BBServerInfo(_BB):
    model_config = ConfigDict(extra="ignore", frozen=True)

    computer_id: str | None = None
    os_version: str | None = None
    server_version: str | None = None
    private_api: bool = False
    helper_connected: bool = False
    detected_imessage: str | None = None


class BBAddress(_BB):
    address: str


class BBContact(_BB):
    display_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    phone_numbers: list[BBAddress] = []
    emails: list[BBAddress] = []
    # Base64 thumbnail, or "" when the contact has no photo.
    avatar: str | None = None

    @property
    def photo(self) -> bytes | None:
        if not self.avatar:
            return None
        try:
            return base64.b64decode(self.avatar, validate=True) or None
        except binascii.Error:
            return None

    @property
    def name(self) -> str:
        full = " ".join(p for p in (self.first_name, self.last_name) if p)
        return self.display_name or full

    @property
    def addresses(self) -> list[str]:
        return [a.address for a in (*self.phone_numbers, *self.emails) if a.address]


class BBTyping(_BB):
    display: bool
    guid: str


class BBReadStatus(_BB):
    chat_guid: str
    read: bool


class BBCallStatus(BaseModel):
    """`ft-call-status-changed` uses snake_case keys."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    uuid: str
    status: str
    address: str | None = None
    is_video: bool = True
    is_outgoing: bool = False


class BBLink(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    link: str
