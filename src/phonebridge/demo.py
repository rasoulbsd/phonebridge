"""A pretend iPhone with fictional data, for trying the UI without a phone."""

from __future__ import annotations

import asyncio
import itertools
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from phonebridge.domain import Contact, Direction, MessageStatus, NewMessage, conversation_key
from phonebridge.protocol import ListedMessage, MapEvent

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from phonebridge.services import MessageRepository

_PEOPLE = {
    "+15550142": "Maya Chen",
    "+15550178": "Jordan Rivera",
    "+15550193": "Sam Okafor",
    "+15550116": "Priya Patel",
    "+15550100": "Lakeside Dental",
}

# (minutes ago, sender, text, read)
_INBOX = [
    (60 * 50, "+15550116", "Happy birthday!! Hope you have the best day", True),
    (60 * 26, "+15550100", "Reminder: cleaning appointment tomorrow at 9:30 AM. Reply C to confirm.", True),
    (60 * 25, "+15550193", "Did you get the photos from the trip? I can resend them by email.", True),
    (60 * 5, "+15550178", "Running 10 min late, grab us a table?", True),
    (
        48,
        "+15550142",
        "Here's the doc for Thursday: https://example.com/plan - mostly the budget section changed, "
        "and I moved the vendor list to the end so it reads better. Let me know if anything looks off "
        "before I send it to the rest of the team.",
        False,
    ),
    (3, "+15550142", "Also, are we still on for lunch?", False),
]

# (minutes ago, recipient, text)
_SENT = [
    (60 * 49, "+15550116", "Thank you so much! Dinner next week?"),
    (60 * 25 - 4, "+15550193", "Got them, they look great"),
    (60 * 5 - 2, "+15550178", "On it"),
    (40, "+15550142", "Looks good, I'll read it properly after lunch"),
]

_LIVE = [
    ("+15550178", "Thanks for yesterday, that place was great"),
    ("+15550193", "Are you free for a quick call later?"),
    ("+15550142", "Booked a table for 12:30"),
]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _listed(handle: str, sender: str, text: str, at: datetime, *, read: bool) -> ListedMessage:
    return ListedMessage(
        handle=handle,
        subject=text[:255],
        sent_at=at,
        sender_name=_PEOPLE.get(sender, ""),
        sender_addressing=sender,
        msg_type="SMS_GSM",
        size=len(text.encode("utf-8")),
        read=read,
    )


async def seed_demo_history(repo: MessageRepository, clock: Callable[[], datetime] = _utcnow) -> None:
    now = clock()
    for minutes, recipient, text in _SENT:
        await repo.add_message(
            NewMessage(
                dedupe_key=f"demo-out:{uuid.uuid4().hex}",
                conversation_id=conversation_key(recipient),
                direction=Direction.OUT,
                address=recipient,
                name=_PEOPLE.get(recipient),
                text=text,
                at=now - timedelta(minutes=minutes),
                read=True,
                status=MessageStatus.SENT,
            )
        )


class DemoSession:
    def __init__(self, clock: Callable[[], datetime], interval: float) -> None:
        self._clock = clock
        self._interval = interval
        now = clock()
        self._texts: dict[str, str] = {}
        self._inbox: list[ListedMessage] = []
        self._handles: Iterator[int] = itertools.count(1)
        self._live = itertools.cycle(_LIVE)
        for minutes, sender, text, read in _INBOX:
            self._add(sender, text, now - timedelta(minutes=minutes), read=read)

    def _add(self, sender: str, text: str, at: datetime, *, read: bool) -> ListedMessage:
        handle = f"demo-{next(self._handles)}"
        self._texts[handle] = text
        item = _listed(handle, sender, text, at, read=read)
        self._inbox.append(item)
        return item

    @property
    def phone_name(self) -> str:
        return "Demo iPhone"

    @property
    def push_enabled(self) -> bool:
        return True

    async def list_inbox(self) -> list[ListedMessage]:
        return list(self._inbox)

    async def fetch_text(self, handle: str) -> str:
        return self._texts.get(handle, "")

    async def send(self, address: str, text: str) -> str | None:
        await asyncio.sleep(0.6)
        return f"demo-sent-{next(self._handles)}-{len(address) + len(text)}"

    async def set_read(self, handle: str, *, read: bool) -> None:
        self._inbox = [m.model_copy(update={"read": read}) if m.handle == handle else m for m in self._inbox]

    async def pull_contacts(self) -> list[Contact]:
        return [Contact(name=name, addresses=[number]) for number, name in _PEOPLE.items()]

    async def next_event(self) -> MapEvent:
        await asyncio.sleep(self._interval)
        sender, text = next(self._live)
        item = self._add(sender, text, self._clock(), read=False)
        return MapEvent(kind="NewMessage", handle=item.handle, folder="TELECOM/MSG/INBOX", msg_type="SMS_GSM")

    async def close(self) -> None:
        return None


class DemoConnector:
    def __init__(self, clock: Callable[[], datetime] = _utcnow, *, interval: float = 25.0) -> None:
        self._clock = clock
        self._interval = interval

    async def connect(self) -> DemoSession:
        await asyncio.sleep(0.8)
        return DemoSession(self._clock, self._interval)

    async def close(self) -> None:
        return None
