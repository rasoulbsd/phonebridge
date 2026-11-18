from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from phonebridge.protocol import ListedMessage, MapEvent, StreamClosedError
from phonebridge.services import PhoneBridge, PhoneUnavailableError
from phonebridge.storage import SqliteRepository

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterable

    from phonebridge.domain import Contact, UiEvent
    from phonebridge.domain.ui import UiUpdate
    from phonebridge.services import PhoneError

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def clock() -> datetime:
    return NOW


def listed(
    handle: str,
    *,
    sender: str = "+15550142",
    name: str = "Maya Chen",
    text: str = "hello",
    minutes_ago: int = 0,
    read: bool = False,
    size: int | None = None,
    attachment_size: int = 0,
) -> ListedMessage:
    return ListedMessage(
        handle=handle,
        subject=text,
        sent_at=NOW - timedelta(minutes=minutes_ago),
        sender_name=name,
        sender_addressing=sender,
        msg_type="SMS_GSM",
        size=len(text.encode()) if size is None else size,
        read=read,
        attachment_size=attachment_size,
    )


class FakeStream:
    """In-memory byte stream: `incoming` is what the remote side will send."""

    def __init__(self, incoming: bytes = b"") -> None:
        self.incoming = bytearray(incoming)
        self.written = bytearray()
        self.closed = False

    def feed(self, data: bytes) -> None:
        self.incoming += data

    async def read_exact(self, size: int) -> bytes:
        if len(self.incoming) < size:
            raise StreamClosedError("no more scripted data")
        chunk = bytes(self.incoming[:size])
        del self.incoming[:size]
        return chunk

    async def write(self, data: bytes) -> None:
        self.written += data

    async def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(
        self,
        inbox: Iterable[ListedMessage] = (),
        *,
        texts: dict[str, str] | None = None,
        contacts: Iterable[Contact] = (),
    ) -> None:
        self.inbox = list(inbox)
        self.texts = dict(texts or {})
        self.contacts = list(contacts)
        self.sent: list[tuple[str, str]] = []
        self.read_calls: list[tuple[str, bool]] = []
        self.fetches: list[str] = []
        self.send_error: PhoneError | None = None
        self.list_error: PhoneError | None = None
        self.contacts_gate: asyncio.Event | None = None
        self.events: asyncio.Queue[MapEvent] = asyncio.Queue()
        self.closed = False
        self.push = True

    @property
    def phone_name(self) -> str:
        return "Test iPhone"

    @property
    def push_enabled(self) -> bool:
        return self.push

    async def list_inbox(self) -> list[ListedMessage]:
        if self.list_error is not None:
            raise self.list_error
        return list(self.inbox)

    async def fetch_text(self, handle: str) -> str:
        self.fetches.append(handle)
        return self.texts[handle]

    async def send(self, address: str, text: str) -> str | None:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append((address, text))
        return f"sent-{len(self.sent)}"

    async def set_read(self, handle: str, *, read: bool) -> None:
        self.read_calls.append((handle, read))

    async def pull_contacts(self) -> list[Contact]:
        if self.contacts_gate is not None:
            await self.contacts_gate.wait()
        return list(self.contacts)

    async def next_event(self) -> MapEvent:
        return await self.events.get()

    async def close(self) -> None:
        self.closed = True

    def notify(self) -> None:
        self.events.put_nowait(MapEvent("NewMessage", "", "TELECOM/MSG/INBOX", "SMS_GSM"))


class FakeConnector:
    def __init__(self, *outcomes: FakeSession | PhoneError) -> None:
        self._outcomes = list(outcomes)
        self.attempts = 0

    async def connect(self) -> FakeSession:
        self.attempts += 1
        if not self._outcomes:
            raise PhoneUnavailableError("phone gone")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def close(self) -> None:
        return None


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[UiEvent] = []
        self._changed = asyncio.Condition()

    async def publish(self, event: UiEvent) -> None:
        async with self._changed:
            self.events.append(event)
            self._changed.notify_all()

    def mark(self) -> int:
        return len(self.events)

    async def wait_for(self, predicate: Callable[[UiEvent], bool], *, since: int = 0) -> UiEvent:
        async with asyncio.timeout(2.0), self._changed:
            while True:
                for event in self.events[since:]:
                    if predicate(event):
                        return event
                await self._changed.wait()


class UpdateRecorder:
    """UI sink for the v2 updates (Mac and phone services)."""

    def __init__(self) -> None:
        self.updates: list[UiUpdate] = []
        self._changed = asyncio.Condition()

    async def publish(self, update: UiUpdate) -> None:
        async with self._changed:
            self.updates.append(update)
            self._changed.notify_all()

    async def wait_for(self, predicate: Callable[[UiUpdate], bool]) -> UiUpdate:
        async with asyncio.timeout(2.0), self._changed:
            while True:
                for update in self.updates:
                    if predicate(update):
                        return update
                await self._changed.wait()


class FakePhoneLink:
    def __init__(self, *, succeeds: bool = True) -> None:
        self.succeeds = succeeds
        self.calls = 0

    async def close_phone_link(self) -> bool:
        self.calls += 1
        return self.succeeds


@pytest.fixture
async def repo() -> AsyncIterator[SqliteRepository]:
    repository = await SqliteRepository.open(":memory:")
    yield repository
    await repository.close()


@pytest.fixture
def sink() -> RecordingSink:
    return RecordingSink()


def make_bridge(
    connector: FakeConnector,
    repo: SqliteRepository,
    sink: RecordingSink,
    *,
    phone_link: FakePhoneLink | None = None,
) -> PhoneBridge:
    # Long poll intervals: refreshes happen only when a test triggers them.
    return PhoneBridge(
        connector, repo, sink, phone_link=phone_link, poll_seconds=60, push_poll_seconds=60, clock=clock
    )


@contextlib.asynccontextmanager
async def running(bridge: PhoneBridge) -> AsyncGenerator[None]:
    task = asyncio.create_task(bridge.run())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
