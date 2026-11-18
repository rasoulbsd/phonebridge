"""The operations the UI can ask for. Mac and Bluetooth backends both implement this."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

    from phonebridge.domain.ui import (
        AppStatus,
        Call,
        Chat,
        ChatMessage,
        ChatSearchHit,
        ContactCard,
        ReactionType,
        ThreadPage,
        UiUpdate,
    )


class UnsupportedError(Exception):
    """The active backend can't do this (e.g. reactions over Bluetooth)."""


class NotFoundError(Exception):
    pass


class RequestError(Exception):
    """The request was understood but failed; the message is shown to the user."""


@dataclass(slots=True, frozen=True)
class Photo:
    content_type: str
    data: bytes


@dataclass(slots=True, frozen=True)
class AttachmentStream:
    content_type: str
    chunks: AsyncIterator[bytes]
    close: Callable[[], Awaitable[None]]
    size: int | None = None


class UiSink(Protocol):
    async def publish(self, update: UiUpdate) -> None: ...


class MessagingService(Protocol):
    async def run(self) -> None: ...

    async def status(self) -> AppStatus: ...

    async def chats(self, query: str | None = None) -> list[Chat]: ...

    async def chat(self, chat_id: str) -> Chat: ...

    async def messages(self, chat_id: str, *, before: str | None, limit: int) -> ThreadPage: ...

    async def send_text(
        self, chat_id: str, text: str, *, reply_to: str | None, temp_id: str | None
    ) -> ChatMessage: ...

    async def send_attachment(
        self, chat_id: str, *, filename: str, content_type: str, data: bytes, temp_id: str | None
    ) -> ChatMessage: ...

    async def new_chat(self, addresses: Sequence[str], text: str) -> ThreadPage: ...

    async def mark_read(self, chat_id: str) -> None: ...

    async def set_typing(self, chat_id: str, *, typing: bool) -> None: ...

    async def rename(self, chat_id: str, title: str) -> Chat: ...

    async def add_participant(self, chat_id: str, address: str) -> Chat: ...

    async def remove_participant(self, chat_id: str, address: str) -> Chat: ...

    async def leave(self, chat_id: str) -> None: ...

    async def react(self, chat_id: str, message_id: str, reaction: ReactionType, *, remove: bool) -> None: ...

    async def edit(self, chat_id: str, message_id: str, text: str) -> None: ...

    async def unsend(self, chat_id: str, message_id: str) -> None: ...

    async def retry(self, chat_id: str, message_id: str) -> ChatMessage: ...

    async def open_attachment(self, attachment_id: str, *, thumb: bool) -> AttachmentStream: ...

    async def search(self, query: str, limit: int) -> list[ChatSearchHit]: ...

    async def contacts(self, query: str, limit: int) -> list[ContactCard]: ...

    async def answer_call(self, call_id: str) -> str: ...

    async def decline_call(self, call_id: str) -> None: ...

    async def start_call(self, chat_id: str, *, video: bool) -> Call: ...

    async def join_call(self, call_id: str) -> str:
        """The link this PC opens to take over a call it placed, once they answered."""
        ...

    async def photo(self, photo_id: str) -> Photo: ...

    async def close_phone_link(self) -> bool: ...

    async def close(self) -> None: ...
