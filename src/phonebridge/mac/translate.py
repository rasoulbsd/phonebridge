"""Pure conversions from BlueBubbles payloads to UI types."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote

from phonebridge.domain import conversation_key
from phonebridge.domain.ui import (
    Attachment,
    Chat,
    ChatMessage,
    Delivery,
    Person,
    ReactionType,
)
from phonebridge.mac.photos import thumbnail

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from phonebridge.domain.ui import Reaction, ReplyRef
    from phonebridge.mac.models import BBAttachment, BBChat, BBContact, BBHandle, BBMessage

GROUP_STYLE = 43
_OBJECT_REPLACEMENT = "￼"
_PLUGIN_PAYLOAD = ".pluginPayloadAttachment"
_NAMES_IN_TITLE = 3
_PHOTO_ID_LENGTH = 20
_REACTION_VERBS = {
    ReactionType.LOVE: "Loved",
    ReactionType.LIKE: "Liked",
    ReactionType.DISLIKE: "Disliked",
    ReactionType.LAUGH: "Laughed at",
    ReactionType.EMPHASIZE: "Emphasized",
    ReactionType.QUESTION: "Questioned",
}
_SERVICES: dict[str, Literal["iMessage", "SMS", "RCS"]] = {"iMessage": "iMessage", "SMS": "SMS", "RCS": "RCS"}
_RELAYED_SERVICES = frozenset({"SMS", "RCS"})
# Messages' error for a recipient that isn't registered with iMessage.
_NOT_ON_IMESSAGE = 22
_TEXT_NOT_RELAYED = (
    "Not sent as a text message. Texts go through your iPhone: check it's on and has "
    "Text Message Forwarding turned on for this Mac (Settings > Apps > Messages)."
)


def from_ms(value: int | None) -> datetime | None:
    return datetime.fromtimestamp(value / 1000, tz=UTC) if value else None


def is_group(guid: str, style: int | None) -> bool:
    return style == GROUP_STYLE or ";+;" in guid


def service_of(guid: str) -> Literal["iMessage", "SMS", "RCS"] | None:
    return _SERVICES.get(guid.split(";", 1)[0])


def reaction_of(message: BBMessage) -> tuple[ReactionType, bool] | None:
    """(type, removed) for tapback messages, None for everything else."""
    kind = message.associated_message_type
    if not kind or not message.associated_message_guid:
        return None
    name = kind.removeprefix("-")
    if name not in ReactionType:
        return None
    return ReactionType(name), kind.startswith("-")


def target_guid(associated: str) -> str:
    # Tapbacks point at "p:<part>/<guid>" (or "bp:<guid>" for older ones).
    if "/" in associated:
        return associated.split("/", 1)[1]
    return associated.removeprefix("bp:")


class Names:
    """Contact names and photos by phone number / email, matched like conversations are.

    Building one shrinks every contact photo, so do it off the event loop.
    """

    def __init__(self, contacts: Iterable[BBContact] = ()) -> None:
        self._by_key: dict[str, str] = {}
        self._photo_by_key: dict[str, str] = {}
        self._photos: dict[str, bytes] = {}
        for contact in contacts:
            image = thumbnail(contact.photo) if contact.photo else None
            # Named by content, so the browser can cache a photo until it changes.
            photo_id = hashlib.sha256(image).hexdigest()[:_PHOTO_ID_LENGTH] if image else None
            if image and photo_id:
                self._photos[photo_id] = image
            for address in contact.addresses:
                key = conversation_key(address)
                if contact.name:
                    self._by_key.setdefault(key, contact.name)
                if photo_id:
                    self._photo_by_key.setdefault(key, photo_id)

    def __len__(self) -> int:
        return len(self._by_key)

    def name(self, address: str | None) -> str | None:
        return self._by_key.get(conversation_key(address)) if address else None

    def photo_url(self, address: str | None) -> str | None:
        photo_id = self._photo_by_key.get(conversation_key(address)) if address else None
        return f"/api/photos/{photo_id}" if photo_id else None

    def photo(self, photo_id: str) -> bytes | None:
        return self._photos.get(photo_id)

    def person(self, address: str) -> Person:
        return Person(address=address, name=self.name(address), photo=self.photo_url(address))

    def short(self, address: str | None) -> str:
        name = self.name(address)
        if name:
            return name.split()[0]
        return address or "Someone"


def chat_title(chat: BBChat, names: Names) -> str:
    if chat.display_name:
        return chat.display_name
    people = [names.short(h.address) for h in chat.participants]
    if not people:
        return names.name(chat.chat_identifier) or chat.chat_identifier or "Unknown"
    if not is_group(chat.guid, chat.style):
        return names.name(chat.participants[0].address) or chat.participants[0].address
    if len(people) <= _NAMES_IN_TITLE:
        return ", ".join(people[:-1]) + f" and {people[-1]}" if len(people) > 1 else people[0]
    return f"{', '.join(people[:2])} and {len(people) - 2} others"


def _attachment_label(attachments: Sequence[BBAttachment]) -> str:
    mime = (attachments[0].mime_type or "") if attachments else ""
    if mime.startswith("image/"):
        return "Photo"
    if mime.startswith("video/"):
        return "Video"
    if mime.startswith("audio/"):
        return "Audio message"
    return "Attachment"


def clean_text(message: BBMessage) -> str:
    return (message.text or "").replace(_OBJECT_REPLACEMENT, "").strip()


def visible_attachments(message: BBMessage) -> list[BBAttachment]:
    return [
        a
        for a in message.attachments
        if not a.hide_attachment and not (a.transfer_name or "").endswith(_PLUGIN_PAYLOAD)
    ]


def event_text(message: BBMessage, names: Names, participants: Sequence[BBHandle]) -> str | None:
    actor = "You" if message.is_from_me else names.short(message.handle.address if message.handle else None)
    other = next((h for h in participants if h.original_rowid == message.other_handle), None)
    other_name = names.short(other.address) if other else "someone"
    match (message.item_type, message.group_action_type):
        case (1, 0):
            return f"{actor} added {other_name} to the conversation"
        case (1, 1):
            return f"{actor} removed {other_name} from the conversation"
        case (2, _):
            title = message.group_title
            return f"{actor} named the conversation “{title}”" if title else f"{actor} removed the name"
        case (3, 0):
            return f"{actor} left the conversation"
        case (3, 1):
            return f"{actor} changed the group photo"
        case (3, 2):
            return f"{actor} removed the group photo"
        case _:
            return None


def preview(message: BBMessage, names: Names, *, group: bool, participants: Sequence[BBHandle] = ()) -> str:
    reaction = reaction_of(message)
    if reaction is not None:
        kind, removed = reaction
        body = "Removed a reaction" if removed else f"{_REACTION_VERBS[kind]} a message"
    elif message.date_retracted:
        body = "Message unsent"
    elif message.item_type:
        return event_text(message, names, participants) or ""
    else:
        attachments = visible_attachments(message)
        body = clean_text(message) or (_attachment_label(attachments) if attachments else "")
        if not body and message.balloon_bundle_id:
            body = "Sent with an iMessage app"
    if group and not message.is_from_me and message.handle:
        return f"{names.short(message.handle.address)}: {body}"
    return body


def delivery_of(message: BBMessage) -> Delivery:
    if not message.is_from_me:
        return Delivery.RECEIVED
    if message.error:
        return Delivery.FAILED
    if message.date_read:
        return Delivery.READ
    if message.date_delivered or message.is_delivered:
        return Delivery.DELIVERED
    return Delivery.SENT


def send_error(message: BBMessage, chat_id: str) -> str | None:
    """Why a message from this Mac wasn't delivered, in words the sender can act on."""
    if not message.is_from_me or not message.error:
        return None
    service = (message.handle.service if message.handle else None) or service_of(chat_id)
    if service in _RELAYED_SERVICES:
        # A Mac has no phone line: texts only leave through an iPhone that relays them.
        return _TEXT_NOT_RELAYED
    if message.error == _NOT_ON_IMESSAGE:
        return "Not delivered: they don't use iMessage."
    return f"Not delivered (error {message.error})"


def attachment_url(guid: str) -> str:
    return f"/api/attachments/{quote(guid, safe='')}"


def attachments_of(message: BBMessage) -> list[Attachment]:
    out: list[Attachment] = []
    for item in visible_attachments(message):
        mime = item.mime_type or "application/octet-stream"
        url = attachment_url(item.guid)
        out.append(
            Attachment(
                id=item.guid,
                name=item.transfer_name or "Attachment",
                mime=mime,
                size=item.total_bytes,
                width=item.width,
                height=item.height,
                url=url,
                thumb_url=f"{url}?thumb=1" if mime.startswith("image/") else None,
            )
        )
    return out


def chat_from_bb(chat: BBChat, names: Names, *, unread: int) -> Chat:
    group = is_group(chat.guid, chat.style)
    last = chat.last_message
    return Chat(
        id=chat.guid,
        title=chat_title(chat, names),
        is_group=group,
        service=service_of(chat.guid),
        participants=[names.person(h.address) for h in chat.participants],
        has_custom_name=bool(chat.display_name),
        # The UI adds "Maya: " itself from last_sender, so the preview text stays bare.
        last_text=preview(last, names, group=False, participants=chat.participants) if last else "",
        last_at=from_ms(last.date_created) if last else None,
        last_from_me=bool(last and last.is_from_me and not last.item_type),
        last_sender=names.person(last.handle.address)
        if group and last and not last.is_from_me and not last.item_type and last.handle
        else None,
        unread=unread,
    )


def message_from_bb(
    message: BBMessage,
    chat_id: str,
    names: Names,
    *,
    reactions: list[Reaction],
    reply_to: ReplyRef | None,
    participants: Sequence[BBHandle],
) -> ChatMessage | None:
    """None for tapbacks: they're folded into the message they react to."""
    if reaction_of(message) is not None:
        return None
    unsent = message.date_retracted is not None
    text = "" if unsent else clean_text(message)
    if not text and message.balloon_bundle_id and not message.item_type:
        text = "Sent with an iMessage app"
    sender = None if message.is_from_me or not message.handle else names.person(message.handle.address)
    return ChatMessage(
        id=message.guid,
        chat_id=chat_id,
        temp_id=message.temp_guid,
        from_me=message.is_from_me,
        sender=sender,
        text=text,
        at=from_ms(message.date_created) or datetime.fromtimestamp(0, tz=UTC),
        status=delivery_of(message),
        delivered_at=from_ms(message.date_delivered),
        read_at=from_ms(message.date_read),
        error=send_error(message, chat_id),
        attachments=[] if unsent else attachments_of(message),
        reactions=reactions,
        reply_to=reply_to,
        edited=message.date_edited is not None and not unsent,
        unsent=unsent,
        event=event_text(message, names, participants) if message.item_type else None,
    )
