"""The iPhone-over-Bluetooth backend, presented through the UI API (1:1 texts only)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from phonebridge.domain import ConversationEvent, Direction, MessageEvent, MessageStatus, StatusEvent
from phonebridge.domain.ui import (
    AppStatus,
    Attachment,
    Chat,
    ChatMessage,
    ChatSearchHit,
    ChatUpdate,
    ContactCard,
    Delivery,
    Features,
    MessageUpdate,
    Mode,
    Person,
    State,
    StatusUpdate,
    ThreadPage,
)
from phonebridge.services.bridge import PhoneBridge
from phonebridge.services.errors import ConversationNotFoundError, InvalidMessageError, MessageNotFoundError
from phonebridge.services.messaging import NotFoundError, RequestError, UnsupportedError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from phonebridge.domain import Conversation, Message, PhoneStatus, UiEvent
    from phonebridge.domain.ui import Call, ReactionType
    from phonebridge.services.messaging import AttachmentStream, Photo, UiSink
    from phonebridge.services.ports import MessageRepository, PhoneConnector, PhoneLinkControl

PHONE_FEATURES = Features(search=True, contacts=True)
NEEDS_MAC = "This needs a Mac. Connect one in Settings for group chats, photos and reactions."

_STATUS = {
    MessageStatus.RECEIVED: Delivery.RECEIVED,
    MessageStatus.SENDING: Delivery.SENDING,
    MessageStatus.SENT: Delivery.SENT,
    MessageStatus.FAILED: Delivery.FAILED,
}


def chat_from_conversation(conversation: Conversation) -> Chat:
    return Chat(
        id=conversation.id,
        title=conversation.name or conversation.address,
        participants=[Person(address=conversation.address, name=conversation.name)],
        last_text=conversation.last_text,
        last_at=conversation.last_at,
        last_from_me=conversation.last_direction is Direction.OUT,
        unread=conversation.unread,
    )


def message_from_v1(message: Message, *, temp_id: str | None = None) -> ChatMessage:
    from_me = message.direction is Direction.OUT
    attachments = (
        [
            Attachment(
                id=f"bt-{message.id}",
                name="Attachment",
                mime="application/octet-stream",
                url="",
                available=False,
            )
        ]
        if message.has_attachment
        else []
    )
    return ChatMessage(
        id=str(message.id),
        chat_id=message.conversation_id,
        temp_id=temp_id,
        from_me=from_me,
        sender=None if from_me else Person(address=message.address),
        text=message.text,
        at=message.at,
        status=_STATUS[message.status],
        error=message.error,
        attachments=attachments,
    )


def status_from_v1(status: PhoneStatus, mode: Mode) -> AppStatus:
    return AppStatus(
        mode=mode,
        state=State(status.state.value),
        device_name=status.phone_name,
        detail=status.detail,
        features=PHONE_FEATURES,
        notifications=status.notifications.value if status.notifications else None,
        last_sync_at=status.last_sync_at,
        contacts_count=status.contacts_count,
    )


class _Translator:
    """Turns the Bluetooth bridge's events into UI updates."""

    def __init__(self, sink: UiSink, mode: Mode) -> None:
        self._sink = sink
        self._mode = mode

    async def publish(self, event: UiEvent) -> None:
        match event:
            case StatusEvent():
                await self._sink.publish(StatusUpdate(status=status_from_v1(event.status, self._mode)))
            case MessageEvent():
                await self._sink.publish(
                    MessageUpdate(
                        message=message_from_v1(event.message),
                        chat=chat_from_conversation(event.conversation),
                    )
                )
            case ConversationEvent():
                await self._sink.publish(ChatUpdate(chat=chat_from_conversation(event.conversation)))


class PhoneService:
    def __init__(
        self,
        connector: PhoneConnector,
        repo: MessageRepository,
        sink: UiSink,
        *,
        mode: Mode = Mode.PHONE,
        phone_link: PhoneLinkControl | None = None,
        poll_seconds: float = 5.0,
        push_poll_seconds: float = 30.0,
    ) -> None:
        self._connector = connector
        self._mode = mode
        self.bridge = PhoneBridge(
            connector,
            repo,
            _Translator(sink, mode),
            phone_link=phone_link,
            poll_seconds=poll_seconds,
            push_poll_seconds=push_poll_seconds,
        )

    async def run(self) -> None:
        await self.bridge.run()

    async def close(self) -> None:
        await self._connector.close()

    async def status(self) -> AppStatus:
        return status_from_v1(await self.bridge.status(), self._mode)

    async def chats(self, query: str | None = None) -> list[Chat]:
        return [chat_from_conversation(c) for c in await self.bridge.conversations(query)]

    async def chat(self, chat_id: str) -> Chat:
        try:
            conversation, _, _ = await self.bridge.messages(chat_id, limit=1)
        except ConversationNotFoundError as exc:
            raise NotFoundError(chat_id) from exc
        return chat_from_conversation(conversation)

    async def messages(self, chat_id: str, *, before: str | None, limit: int) -> ThreadPage:
        before_id = int(before) if before and before.isdigit() else None
        try:
            conversation, messages, has_more = await self.bridge.messages(
                chat_id, before_id=before_id, limit=limit
            )
        except ConversationNotFoundError as exc:
            raise NotFoundError(chat_id) from exc
        return ThreadPage(
            chat=chat_from_conversation(conversation),
            messages=[message_from_v1(m) for m in messages],
            cursor=str(messages[0].id) if has_more and messages else None,
        )

    async def _send(self, address: str, text: str, temp_id: str | None) -> ChatMessage:
        try:
            message = await self.bridge.send(address, text)
        except InvalidMessageError as exc:
            raise RequestError(str(exc)) from exc
        return message_from_v1(message, temp_id=temp_id)

    async def send_text(
        self, chat_id: str, text: str, *, reply_to: str | None, temp_id: str | None
    ) -> ChatMessage:
        if reply_to:
            raise UnsupportedError(NEEDS_MAC)
        chat = await self.chat(chat_id)
        return await self._send(chat.participants[0].address, text, temp_id)

    async def send_attachment(
        self, chat_id: str, *, filename: str, content_type: str, data: bytes, temp_id: str | None
    ) -> ChatMessage:
        raise UnsupportedError(NEEDS_MAC)

    async def new_chat(self, addresses: Sequence[str], text: str) -> ThreadPage:
        if len(addresses) != 1:
            raise UnsupportedError(NEEDS_MAC)
        message = await self._send(addresses[0], text, None)
        return await self.messages(message.chat_id, before=None, limit=50)

    async def mark_read(self, chat_id: str) -> None:
        await self.bridge.mark_read(chat_id)

    async def set_typing(self, chat_id: str, *, typing: bool) -> None:
        return None

    async def rename(self, chat_id: str, title: str) -> Chat:
        raise UnsupportedError(NEEDS_MAC)

    async def add_participant(self, chat_id: str, address: str) -> Chat:
        raise UnsupportedError(NEEDS_MAC)

    async def remove_participant(self, chat_id: str, address: str) -> Chat:
        raise UnsupportedError(NEEDS_MAC)

    async def leave(self, chat_id: str) -> None:
        raise UnsupportedError(NEEDS_MAC)

    async def react(self, chat_id: str, message_id: str, reaction: ReactionType, *, remove: bool) -> None:
        raise UnsupportedError(NEEDS_MAC)

    async def edit(self, chat_id: str, message_id: str, text: str) -> None:
        raise UnsupportedError(NEEDS_MAC)

    async def unsend(self, chat_id: str, message_id: str) -> None:
        raise UnsupportedError(NEEDS_MAC)

    async def retry(self, chat_id: str, message_id: str) -> ChatMessage:
        if not message_id.isdigit():
            raise NotFoundError(message_id)
        try:
            return message_from_v1(await self.bridge.retry(int(message_id)))
        except MessageNotFoundError as exc:
            raise NotFoundError(message_id) from exc
        except InvalidMessageError as exc:
            raise RequestError(str(exc)) from exc

    async def open_attachment(self, attachment_id: str, *, thumb: bool) -> AttachmentStream:
        raise NotFoundError(attachment_id)

    async def search(self, query: str, limit: int) -> list[ChatSearchHit]:
        return [
            ChatSearchHit(message=message_from_v1(hit.message), chat=chat_from_conversation(hit.conversation))
            for hit in await self.bridge.search(query, limit)
        ]

    async def contacts(self, query: str, limit: int) -> list[ContactCard]:
        return [
            ContactCard(name=c.name, addresses=c.addresses) for c in await self.bridge.contacts(query, limit)
        ]

    async def answer_call(self, call_id: str) -> str:
        raise UnsupportedError(NEEDS_MAC)

    async def decline_call(self, call_id: str) -> None:
        raise UnsupportedError(NEEDS_MAC)

    async def start_call(self, chat_id: str, *, video: bool) -> Call:
        raise UnsupportedError(NEEDS_MAC)

    async def join_call(self, call_id: str) -> str:
        raise UnsupportedError(NEEDS_MAC)

    async def photo(self, photo_id: str) -> Photo:
        raise NotFoundError(photo_id)

    async def close_phone_link(self) -> bool:
        return await self.bridge.close_phone_link()
