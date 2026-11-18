"""Interfaces the services depend on. Concrete adapters are wired in __main__."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from phonebridge.domain import (
        Contact,
        Conversation,
        Message,
        MessageStatus,
        NewMessage,
        SearchHit,
        UiEvent,
    )
    from phonebridge.protocol import ListedMessage, MapEvent


class PhoneError(Exception):
    """A phone problem with a message the UI can show as-is."""


class PhoneNotPairedError(PhoneError):
    pass


class PhoneBusyError(PhoneError):
    """Another app (Phone Link) holds the phone's message service."""


class PhoneUnavailableError(PhoneError):
    """Paired but not reachable: out of range, Bluetooth off, or sharing disabled."""


class PhoneDisconnectedError(PhoneError):
    """An established connection dropped."""


class PhoneRequestError(PhoneError):
    """The phone refused one request; the connection itself is still fine."""


class PhoneSession(Protocol):
    @property
    def phone_name(self) -> str: ...

    @property
    def push_enabled(self) -> bool: ...

    async def list_inbox(self) -> list[ListedMessage]: ...

    async def fetch_text(self, handle: str) -> str: ...

    async def send(self, address: str, text: str) -> str | None: ...

    async def set_read(self, handle: str, *, read: bool) -> None: ...

    async def pull_contacts(self) -> list[Contact]: ...

    async def next_event(self) -> MapEvent:
        """Wait for the phone to report a change; callers apply their own timeout."""
        ...

    async def close(self) -> None: ...


class PhoneConnector(Protocol):
    async def connect(self) -> PhoneSession: ...

    async def close(self) -> None: ...


class PhoneLinkControl(Protocol):
    async def close_phone_link(self) -> bool: ...


class EventSink(Protocol):
    async def publish(self, event: UiEvent) -> None: ...


class MessageRepository(Protocol):
    async def add_message(self, message: NewMessage) -> Message | None: ...

    async def get_message(self, message_id: int) -> Message | None: ...

    async def update_message_status(
        self,
        message_id: int,
        status: MessageStatus,
        *,
        error: str | None = None,
        phone_handle: str | None = None,
        session_id: str | None = None,
    ) -> Message: ...

    async def known_keys(self, keys: Iterable[str]) -> set[str]: ...

    async def attach_handles(self, handles: Mapping[str, str], session_id: str) -> None: ...

    async def mark_read_by_keys(self, keys: Iterable[str]) -> list[str]: ...

    async def mark_conversation_read(self, conversation_id: str) -> list[Message]: ...

    async def get_conversation(self, conversation_id: str) -> Conversation | None: ...

    async def list_conversations(self, query: str | None = None) -> list[Conversation]: ...

    async def list_messages(
        self, conversation_id: str, *, before_id: int | None, limit: int
    ) -> tuple[list[Message], bool]: ...

    async def search(self, query: str, limit: int) -> list[SearchHit]: ...

    async def replace_contacts(self, contacts: Sequence[Contact]) -> None: ...

    async def search_contacts(self, query: str, limit: int) -> list[Contact]: ...

    async def contacts_count(self) -> int: ...

    async def get_meta(self, key: str) -> str | None: ...

    async def set_meta(self, key: str, value: str) -> None: ...
