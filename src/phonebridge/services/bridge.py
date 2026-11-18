"""Keeps the phone connected, saves what it shares, and sends replies."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, NoReturn

from phonebridge.domain import (
    ConnectionState,
    ConversationEvent,
    Direction,
    MessageEvent,
    MessageStatus,
    NewMessage,
    NotificationMode,
    PhoneStatus,
    StatusEvent,
    conversation_key,
    normalize_address,
)
from phonebridge.services.errors import ConversationNotFoundError, InvalidMessageError, MessageNotFoundError
from phonebridge.services.ports import (
    PhoneBusyError,
    PhoneDisconnectedError,
    PhoneError,
    PhoneNotPairedError,
    PhoneRequestError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from phonebridge.domain import Contact, Conversation, Message, SearchHit
    from phonebridge.protocol import ListedMessage
    from phonebridge.services.ports import (
        EventSink,
        MessageRepository,
        PhoneConnector,
        PhoneLinkControl,
        PhoneSession,
    )

logger = logging.getLogger(__name__)

NOT_CONNECTED = "Your iPhone isn't connected."
CONNECTION_LOST = "Lost the connection to your iPhone. Reconnecting..."
MAX_TEXT_LENGTH = 4000
CONTACTS_SYNCED_AT = "contacts_synced_at"
_RECONNECT_DELAY = 2.0
_BUSY_RETRY = 10.0
_NOT_PAIRED_RETRY = 15.0
_BACKOFF = (3.0, 5.0, 10.0, 20.0, 30.0)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def listing_key(item: ListedMessage) -> str:
    # The iPhone does not keep message handles stable across connections, so
    # a saved message is recognised by its content instead.
    raw = f"in|{item.sender_addressing}|{item.sent_at.isoformat()}|{item.size}|{item.subject[:64]}"
    return hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()


class PhoneBridge:
    def __init__(
        self,
        connector: PhoneConnector,
        repo: MessageRepository,
        sink: EventSink,
        *,
        phone_link: PhoneLinkControl | None = None,
        poll_seconds: float = 5.0,
        push_poll_seconds: float = 30.0,
        contacts_max_age: timedelta = timedelta(hours=12),
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._connector = connector
        self._repo = repo
        self._sink = sink
        self._phone_link = phone_link
        self._poll_seconds = poll_seconds
        self._push_poll_seconds = push_poll_seconds
        self._contacts_max_age = contacts_max_age
        self._clock = clock
        self._status = PhoneStatus(state=ConnectionState.CONNECTING)
        self._last_sync: datetime | None = None
        self._session: PhoneSession | None = None
        self._session_id: str | None = None
        self._outbox: asyncio.Queue[int] = asyncio.Queue()
        self._wake = asyncio.Event()
        self._kick = asyncio.Event()

    # Lifecycle

    async def run(self) -> None:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._connection_loop())
            tg.create_task(self._send_worker())

    def reconnect_now(self) -> None:
        self._wake.set()

    # Queries

    async def status(self) -> PhoneStatus:
        return self._status.model_copy(
            update={"last_sync_at": self._last_sync, "contacts_count": await self._repo.contacts_count()}
        )

    async def conversations(self, query: str | None = None) -> list[Conversation]:
        return await self._repo.list_conversations(query)

    async def messages(
        self, conversation_id: str, *, before_id: int | None = None, limit: int = 50
    ) -> tuple[Conversation, list[Message], bool]:
        conversation = await self._repo.get_conversation(conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(conversation_id)
        messages, has_more = await self._repo.list_messages(conversation_id, before_id=before_id, limit=limit)
        return conversation, messages, has_more

    async def search(self, query: str, limit: int = 50) -> list[SearchHit]:
        return await self._repo.search(query, limit) if query.strip() else []

    async def contacts(self, query: str, limit: int = 20) -> list[Contact]:
        return await self._repo.search_contacts(query, limit)

    # Commands

    async def send(self, address: str, text: str) -> Message:
        address = address.strip()
        if not address or not text.strip():
            raise InvalidMessageError("A recipient and some text are required.")
        if len(text) > MAX_TEXT_LENGTH:
            raise InvalidMessageError(f"Messages are limited to {MAX_TEXT_LENGTH} characters.")
        connected = self._session is not None
        saved = await self._repo.add_message(
            NewMessage(
                dedupe_key=f"out:{uuid.uuid4().hex}",
                conversation_id=conversation_key(address),
                direction=Direction.OUT,
                address=normalize_address(address),
                name=None,
                text=text,
                at=self._clock(),
                read=True,
                status=MessageStatus.SENDING if connected else MessageStatus.FAILED,
                error=None if connected else NOT_CONNECTED,
            )
        )
        if saved is None:
            raise InvalidMessageError("Could not save the message.")
        await self._publish_message(saved)
        if connected:
            self._outbox.put_nowait(saved.id)
        return saved

    async def retry(self, message_id: int) -> Message:
        message = await self._repo.get_message(message_id)
        if message is None:
            raise MessageNotFoundError(str(message_id))
        if message.direction is not Direction.OUT or message.status is not MessageStatus.FAILED:
            raise InvalidMessageError("Only failed outgoing messages can be retried.")
        if self._session is None:
            updated = await self._repo.update_message_status(
                message_id, MessageStatus.FAILED, error=NOT_CONNECTED
            )
        else:
            updated = await self._repo.update_message_status(message_id, MessageStatus.SENDING)
            self._outbox.put_nowait(message_id)
        await self._publish_message(updated)
        return updated

    async def mark_read(self, conversation_id: str) -> None:
        changed = await self._repo.mark_conversation_read(conversation_id)
        if not changed:
            return
        conversation = await self._repo.get_conversation(conversation_id)
        if conversation is not None:
            await self._sink.publish(ConversationEvent(conversation=conversation))
        session, session_id = self._session, self._session_id
        if session is None:
            return
        for message in changed:
            if not message.phone_handle or message.session_id != session_id:
                continue
            try:
                await session.set_read(message.phone_handle, read=True)
            except PhoneRequestError as exc:
                logger.info("phone refused read-status update: %s", exc)
            except PhoneDisconnectedError:
                self._kick.set()
                return

    async def close_phone_link(self) -> bool:
        if self._phone_link is None:
            return False
        closed = await self._phone_link.close_phone_link()
        if closed:
            self.reconnect_now()
        return closed

    # Connection handling

    async def _connection_loop(self) -> None:
        failures = 0
        while True:
            if self._status.state is ConnectionState.CONNECTED:
                await self._set_status(ConnectionState.CONNECTING, detail=None)
            try:
                session = await self._connector.connect()
            except PhoneBusyError as exc:
                await self._set_status(ConnectionState.BUSY, detail=str(exc))
                delay = _BUSY_RETRY
            except PhoneNotPairedError as exc:
                await self._set_status(ConnectionState.NO_PHONE, detail=str(exc))
                delay = _NOT_PAIRED_RETRY
            except PhoneError as exc:
                failures += 1
                await self._set_status(ConnectionState.DISCONNECTED, detail=str(exc))
                delay = _BACKOFF[min(failures, len(_BACKOFF)) - 1]
            else:
                failures = 0
                await self._run_session(session)
                delay = _RECONNECT_DELAY
            await self._sleep_or_wake(delay)

    async def _run_session(self, session: PhoneSession) -> None:
        self._session = session
        self._session_id = uuid.uuid4().hex
        logger.info("phone connected: %s", session.phone_name)
        try:
            await self._set_status(
                ConnectionState.CONNECTED,
                phone_name=session.phone_name,
                detail=None,
                notifications=self._notification_mode(session),
            )
            async with asyncio.TaskGroup() as tg:
                # Thousands of contacts take ~30 s over Bluetooth; messages shouldn't wait.
                tg.create_task(self._sync_contacts_if_stale(session))
                await self._refresh(session)
                await self._watch(session)
        except* PhoneError as group:
            logger.warning("phone session ended: %s", group.exceptions[0])
        finally:
            self._session = None
            self._session_id = None
            with contextlib.suppress(PhoneError):
                await session.close()
            await self._set_status(ConnectionState.DISCONNECTED, detail=CONNECTION_LOST, notifications=None)

    async def _watch(self, session: PhoneSession) -> NoReturn:
        while True:
            await self._wait_for_activity(session)
            await self._set_status(ConnectionState.CONNECTED, notifications=self._notification_mode(session))
            await self._refresh(session)

    async def _wait_for_activity(self, session: PhoneSession) -> None:
        timeout = self._push_poll_seconds if session.push_enabled else self._poll_seconds
        event_task = asyncio.create_task(session.next_event())
        kick_task = asyncio.create_task(self._kick.wait())
        done, pending = await asyncio.wait(
            {event_task, kick_task}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        self._kick.clear()
        if event_task in done:
            event = event_task.result()
            logger.debug("phone event %s for %s", event.kind, event.folder)

    async def _sleep_or_wake(self, delay: float) -> None:
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(delay):
                await self._wake.wait()
        self._wake.clear()

    @staticmethod
    def _notification_mode(session: PhoneSession) -> NotificationMode:
        return NotificationMode.PUSH if session.push_enabled else NotificationMode.POLLING

    async def _set_status(self, state: ConnectionState, **changes: object) -> None:
        updated = self._status.model_copy(update={"state": state, **changes})
        if updated == self._status:
            return
        self._status = updated
        await self._sink.publish(StatusEvent(status=await self.status()))

    # Syncing

    async def _sync_contacts_if_stale(self, session: PhoneSession) -> None:
        last = await self._repo.get_meta(CONTACTS_SYNCED_AT)
        now = self._clock()
        if last and now - datetime.fromisoformat(last) < self._contacts_max_age:
            return
        try:
            contacts = await session.pull_contacts()
        except PhoneRequestError as exc:
            logger.warning("could not read contacts from the phone: %s", exc)
            return
        await self._repo.replace_contacts(contacts)
        await self._repo.set_meta(CONTACTS_SYNCED_AT, now.isoformat())
        logger.info("synced %d contacts", len(contacts))
        await self._sink.publish(StatusEvent(status=await self.status()))

    async def _refresh(self, session: PhoneSession) -> None:
        listed = await session.list_inbox()
        session_id = self._session_id or ""
        by_key = {listing_key(item): item for item in listed}
        known = await self._repo.known_keys(by_key.keys())
        fresh = sorted(
            ((key, item) for key, item in by_key.items() if key not in known),
            key=lambda pair: pair[1].sent_at,
        )
        for key, item in fresh:
            text = item.subject if item.has_full_text else await self._fetch_full_text(session, item)
            address = item.sender_addressing or item.sender_name or "Unknown"
            saved = await self._repo.add_message(
                NewMessage(
                    dedupe_key=key,
                    conversation_id=conversation_key(address),
                    direction=Direction.IN,
                    address=address,
                    name=item.sender_name or None,
                    text=text,
                    at=item.sent_at,
                    read=item.read,
                    status=MessageStatus.RECEIVED,
                    has_attachment=item.attachment_size > 0,
                    phone_handle=item.handle,
                    session_id=session_id,
                )
            )
            if saved is not None:
                await self._publish_message(saved)
        await self._repo.attach_handles({key: item.handle for key, item in by_key.items()}, session_id)
        read_on_phone = [key for key, item in by_key.items() if item.read and key in known]
        for conversation_id in await self._repo.mark_read_by_keys(read_on_phone):
            conversation = await self._repo.get_conversation(conversation_id)
            if conversation is not None:
                await self._sink.publish(ConversationEvent(conversation=conversation))
        self._last_sync = self._clock()

    async def _fetch_full_text(self, session: PhoneSession, item: ListedMessage) -> str:
        try:
            text = await session.fetch_text(item.handle)
        except PhoneRequestError as exc:
            logger.info("could not fetch full message text: %s", exc)
            return item.subject
        if not item.read:
            # Downloading a message can mark it read on the iPhone; put it back.
            with contextlib.suppress(PhoneRequestError):
                await session.set_read(item.handle, read=False)
        return text or item.subject

    # Sending

    async def _send_worker(self) -> None:
        while True:
            message_id = await self._outbox.get()
            message = await self._repo.get_message(message_id)
            if message is None or message.status is not MessageStatus.SENDING:
                continue
            session, session_id = self._session, self._session_id
            if session is None:
                updated = await self._repo.update_message_status(
                    message_id, MessageStatus.FAILED, error=NOT_CONNECTED
                )
            else:
                try:
                    handle = await session.send(message.address, message.text)
                except PhoneError as exc:
                    updated = await self._repo.update_message_status(
                        message_id, MessageStatus.FAILED, error=str(exc)
                    )
                    if isinstance(exc, PhoneDisconnectedError):
                        self._kick.set()
                else:
                    updated = await self._repo.update_message_status(
                        message_id, MessageStatus.SENT, phone_handle=handle, session_id=session_id
                    )
            await self._publish_message(updated)

    async def _publish_message(self, message: Message) -> None:
        conversation = await self._repo.get_conversation(message.conversation_id)
        if conversation is not None:
            await self._sink.publish(MessageEvent(message=message, conversation=conversation))
