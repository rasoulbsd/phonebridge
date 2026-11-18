"""Full iMessage through a Mac running BlueBubbles Server."""

from __future__ import annotations

import asyncio
import collections
import contextlib
import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from pydantic import ValidationError

from phonebridge.domain.ui import (
    AppStatus,
    Attachment,
    Call,
    CallStatus,
    CallUpdate,
    ChatMessage,
    ChatRemoved,
    ChatSearchHit,
    ChatUpdate,
    ContactCard,
    Delivery,
    Features,
    MacInfo,
    MessageUpdate,
    Mode,
    Reaction,
    ReplyRef,
    State,
    StatusUpdate,
    ThreadPage,
    TypingUpdate,
)
from phonebridge.mac.call_helper import CallHelperError, CallHelperUnavailableError, CallPhase
from phonebridge.mac.client import BlueBubblesAuthError, BlueBubblesError, BlueBubblesUnavailableError
from phonebridge.mac.events import DISCONNECTED
from phonebridge.mac.models import BBCallStatus, BBChat, BBMessage, BBReadStatus, BBServerInfo, BBTyping
from phonebridge.mac.translate import (
    Names,
    chat_from_bb,
    clean_text,
    is_group,
    message_from_bb,
    preview,
    reaction_of,
    target_guid,
)
from phonebridge.services.messaging import (
    AttachmentStream,
    NotFoundError,
    Photo,
    RequestError,
    UnsupportedError,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from phonebridge.domain.ui import Chat, Person, ReactionType
    from phonebridge.mac.call_helper import HelperCall
    from phonebridge.mac.client import BlueBubblesClient
    from phonebridge.mac.events import MacEvents
    from phonebridge.mac.models import BBContact, BBHandle
    from phonebridge.services.messaging import UiSink

logger = logging.getLogger(__name__)

NEEDS_PRIVATE_API = (
    "This needs the BlueBubbles Private API on the Mac (System Integrity Protection off, Private API on)."
)
# Re-check the Mac's capabilities this often (the Private API helper can connect late).
_REFRESH_EVERY = 60.0
_RETRY_DELAYS = (3.0, 5.0, 10.0, 20.0, 30.0)
_AUTH_RETRY = 30.0
_CACHE_SIZE = 5000
_REPLY_FETCH_BUDGET = 10
_MACOS_VERSION = re.compile(r"(\d+)(?:\.(\d+))?")
_MIN_EDIT_MACOS = 13
_MIN_FACETIME_MACOS = 12
_GROUP_EVENTS = frozenset(
    {
        "group-name-change",
        "participant-added",
        "participant-removed",
        "participant-left",
        "group-icon-changed",
    }
)
_CALL_STATUS = {
    "incoming": CallStatus.INCOMING,
    "disconnected": CallStatus.ENDED,
    "connected": CallStatus.CONNECTED,
}
# Where a call placed through the Mac's FaceTime helper stands, as the UI sees it.
_CALL_PHASES = {
    CallPhase.CALLING: CallStatus.OUTGOING,
    CallPhase.ANSWERED: CallStatus.ANSWERED,
    CallPhase.WAITING_FOR_PC: CallStatus.READY,
    CallPhase.JOINING: CallStatus.READY,
    CallPhase.ON_PC: CallStatus.CONNECTED,
    CallPhase.ENDED: CallStatus.ENDED,
    CallPhase.FAILED: CallStatus.ENDED,
}
_CALL_POLL = 1.0
# Missed polls (Mac unreachable) before a call is given up on.
_CALL_MISSES = 15
NEEDS_CALL_HELPER = (
    "Calling straight from this PC needs the Phonebridge FaceTime helper on the Mac "
    "(packaging/mac/facetime-helper)."
)
_IMAGE_TYPES = ((b"\x89PNG", "image/png"), (b"GIF8", "image/gif"))


def _utcnow() -> datetime:
    return datetime.now(UTC)


def macos_major(version: str | None) -> int:
    match = _MACOS_VERSION.search(version or "")
    return int(match.group(1)) if match else 0


def features_for(info: BBServerInfo) -> Features:
    private = info.private_api and info.helper_connected
    major = macos_major(info.os_version)
    return Features(
        groups=True,
        attachments=True,
        send_attachments=True,
        reactions=private,
        replies=private,
        edit=private and major >= _MIN_EDIT_MACOS,
        unsend=private and major >= _MIN_EDIT_MACOS,
        typing=private,
        read_receipts=True,
        manage_groups=private,
        new_group=private,
        facetime=private and major >= _MIN_FACETIME_MACOS,
        search=True,
        contacts=True,
    )


class CallHelper(Protocol):
    """Phonebridge's helper inside FaceTime on the Mac (phonebridge.mac.call_helper)."""

    async def available(self) -> bool: ...

    async def dial(self, handle: str, *, video: bool) -> HelperCall: ...

    async def call(self, call_id: str) -> HelperCall: ...

    async def end(self, call_id: str) -> HelperCall: ...

    async def close(self) -> None: ...


@dataclass(slots=True)
class _PlacedCall:
    call: Call
    link: str | None = None
    misses: int = 0


def _image_type(data: bytes) -> str:
    for magic, mime in _IMAGE_TYPES:
        if data.startswith(magic):
            return mime
    return "image/jpeg"


@dataclass(slots=True, frozen=True)
class _ReactionState:
    type: ReactionType
    removed: bool
    at: int
    from_me: bool
    address: str | None


@dataclass(slots=True)
class _Outgoing:
    temp_id: str
    chat_id: str
    text: str = ""
    reply_to: str | None = None
    filename: str | None = None
    content_type: str = "application/octet-stream"
    data: bytes = b""

    def placeholder(self, status: Delivery, *, at: datetime, error: str | None = None) -> ChatMessage:
        attachments = (
            [
                Attachment(
                    id=f"temp:{self.temp_id}",
                    name=self.filename,
                    mime=self.content_type,
                    size=len(self.data),
                    url="",
                    available=False,
                )
            ]
            if self.filename
            else []
        )
        return ChatMessage(
            id=f"temp:{self.temp_id}",
            chat_id=self.chat_id,
            temp_id=self.temp_id,
            from_me=True,
            text=self.text,
            at=at,
            status=status,
            error=error,
            attachments=attachments,
        )


class MacService:
    def __init__(
        self,
        client: BlueBubblesClient,
        events_factory: Callable[[], MacEvents],
        sink: UiSink,
        *,
        clock: Callable[[], datetime] = _utcnow,
        refresh_every: float = _REFRESH_EVERY,
        calls: CallHelper | None = None,
        call_poll: float = _CALL_POLL,
    ) -> None:
        self._client = client
        self._calls = calls
        self._call_poll = call_poll
        self._direct_calls = False
        self._placed: dict[str, _PlacedCall] = {}
        self._placed_changed = asyncio.Event()
        self._refresh_every = refresh_every
        self._events_factory = events_factory
        self._sink = sink
        self._clock = clock
        self._status = AppStatus(mode=Mode.MAC, state=State.CONNECTING, mac=MacInfo(url=client.base_url))
        self._features = Features()
        self._private = False
        self._names = Names()
        self._contacts: list[BBContact] = []
        self._chats: dict[str, BBChat] = {}
        self._unread: dict[str, int] = {}
        self._messages: collections.OrderedDict[str, BBMessage] = collections.OrderedDict()
        self._message_chat: dict[str, str] = {}
        self._reactions: dict[str, dict[str, _ReactionState]] = {}
        self._pending: dict[str, _Outgoing] = {}
        self._outbox: asyncio.Queue[_Outgoing] = asyncio.Queue()
        # The send being handed to the Mac right now (the worker sends one at a time).
        self._inflight: _Outgoing | None = None
        self._wake = asyncio.Event()
        self._last_sync: datetime | None = None

    # Lifecycle

    async def run(self) -> None:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._send_worker())
            tg.create_task(self._connection_loop())
            tg.create_task(self._call_worker())

    async def close(self) -> None:
        await self._client.close()
        if self._calls is not None:
            await self._calls.close()

    async def _connection_loop(self) -> None:
        failures = 0
        while True:
            events = self._events_factory()
            delay = _RETRY_DELAYS[min(failures, len(_RETRY_DELAYS) - 1)]
            try:
                await self._sync()
                await events.connect()
                await self._set_state(State.CONNECTED, detail=None)
                failures = 0
                await self._pump(events)
                await self._set_state(
                    State.DISCONNECTED, detail="Lost the connection to the Mac. Reconnecting..."
                )
                delay = _RETRY_DELAYS[0]
            except BlueBubblesAuthError:
                await self._set_state(
                    State.AUTH_FAILED, detail="The Mac rejected the password. Update it in Settings."
                )
                delay = _AUTH_RETRY
            except BlueBubblesUnavailableError as exc:
                failures += 1
                await self._set_state(
                    State.DISCONNECTED,
                    detail=f"{exc} Make sure the Mac is awake and BlueBubbles Server is running. Retrying...",
                )
            except BlueBubblesError as exc:
                failures += 1
                await self._set_state(State.DISCONNECTED, detail=f"The Mac reported a problem: {exc}")
            finally:
                await events.close()
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(delay):
                    await self._wake.wait()
            self._wake.clear()

    async def _sync(self) -> None:
        await self._check_call_helper()
        await self._apply_info(await self._client.server_info())
        self._contacts = await self._client.contacts()
        self._names = await asyncio.to_thread(Names, self._contacts)
        chats = [c for c in await self._client.chats() if not c.is_archived]
        self._chats = {c.guid: c for c in chats}
        for chat in chats:
            last = chat.last_message
            if last is not None and not last.is_from_me and not last.date_read:
                self._unread[chat.guid] = max(1, await self._client.unread_count(chat.guid))
            else:
                self._unread.setdefault(chat.guid, 0)
        self._last_sync = self._clock()

    async def _refresh_chats(self) -> None:
        """Picks up chats that appeared without a live event, e.g. history arriving from iCloud."""
        for chat in await self._client.chats():
            if chat.is_archived:
                continue
            old = self._chats.get(chat.guid)
            old_last = old.last_message.guid if old and old.last_message else None
            new_last = chat.last_message.guid if chat.last_message else None
            if (
                old is not None
                and old_last == new_last
                and old.display_name == chat.display_name
                and len(old.participants) == len(chat.participants)
            ):
                continue
            self._chats[chat.guid] = chat
            if old is None:
                last = chat.last_message
                unread = last is not None and not last.is_from_me and not last.date_read
                self._unread[chat.guid] = max(1, await self._client.unread_count(chat.guid)) if unread else 0
            await self._sink.publish(ChatUpdate(chat=self._chat_out(chat.guid)))
        self._last_sync = self._clock()

    async def _apply_info(self, info: BBServerInfo) -> None:
        self._private = info.private_api and info.helper_connected
        self._features = features_for(info).model_copy(update={"direct_calls": self._direct_calls})
        device = (info.computer_id or "").split("@")[-1] or "Mac"
        self._status = self._status.model_copy(
            update={
                "device_name": device,
                "features": self._features,
                "notifications": "push",
                "mac": MacInfo(
                    url=self._client.base_url,
                    server_version=info.server_version,
                    macos_version=info.os_version,
                    private_api=self._private,
                ),
            }
        )

    async def _pump(self, events: MacEvents) -> None:
        while True:
            try:
                async with asyncio.timeout(self._refresh_every):
                    name, data = await events.next()
            except TimeoutError:
                before = self._status
                await self._check_call_helper()
                await self._apply_info(await self._client.server_info())
                if self._status != before:
                    await self._publish_status()
                await self._refresh_chats()
                continue
            if name == DISCONNECTED:
                return
            try:
                await self._handle(name, data)
            except (ValidationError, json.JSONDecodeError) as exc:
                logger.warning("ignored malformed %s event: %s", name, exc)
            except BlueBubblesUnavailableError:
                raise
            except BlueBubblesError as exc:
                logger.warning("could not process %s: %s", name, exc)

    async def _check_call_helper(self) -> None:
        self._direct_calls = self._calls is not None and await self._calls.available()

    async def _set_state(self, state: State, *, detail: str | None) -> None:
        updated = self._status.model_copy(update={"state": state, "detail": detail})
        if updated != self._status:
            self._status = updated
            await self._publish_status()

    async def _publish_status(self) -> None:
        await self._sink.publish(StatusUpdate(status=await self.status()))

    # Events

    async def _handle(self, name: str, data: object) -> None:
        if name in ("new-message", "updated-message", "message-send-error"):
            await self._ingest(BBMessage.model_validate(data), new=name == "new-message")
        elif name in _GROUP_EVENTS:
            message = BBMessage.model_validate(data)
            await self._ingest(message, new=True)
            for ref in message.chats:
                await self._refresh_chat(ref.guid)
                await self._sink.publish(ChatUpdate(chat=self._chat_out(ref.guid)))
        elif name == "typing-indicator":
            typing = BBTyping.model_validate(data)
            await self._sink.publish(TypingUpdate(chat_id=typing.guid, typing=typing.display))
        elif name == "chat-read-status-changed":
            read = BBReadStatus.model_validate(data)
            if read.read and read.chat_guid in self._chats:
                self._unread[read.chat_guid] = 0
                await self._sink.publish(ChatUpdate(chat=self._chat_out(read.chat_guid)))
        elif name == "ft-call-status-changed":
            await self._publish_call(BBCallStatus.model_validate(data))
        elif name == "incoming-facetime":
            # Without "FaceTime Calling (Experimental)" the server only sends a JSON string: no call id.
            raw: object = json.loads(data) if isinstance(data, str) else data
            caller = raw.get("caller") if isinstance(raw, dict) else None
            await self._sink.publish(
                CallUpdate(
                    call=Call(
                        id=f"legacy:{uuid.uuid4().hex}",
                        status=CallStatus.INCOMING,
                        caller=self._names.person(str(caller)) if caller else None,
                    )
                )
            )

    async def _publish_call(self, status: BBCallStatus) -> None:
        if status.is_outgoing:
            return
        call_status = _CALL_STATUS.get(status.status, CallStatus.RINGING)
        await self._sink.publish(
            CallUpdate(
                call=Call(
                    id=status.uuid,
                    status=call_status,
                    caller=self._names.person(status.address) if status.address else None,
                    is_video=status.is_video,
                )
            )
        )

    def _remember(self, message: BBMessage, chat_id: str | None) -> None:
        self._messages[message.guid] = message
        self._messages.move_to_end(message.guid)
        while len(self._messages) > _CACHE_SIZE:
            self._messages.popitem(last=False)
        if chat_id:
            self._message_chat[message.guid] = chat_id

    def _apply_reaction(self, message: BBMessage) -> str | None:
        reaction = reaction_of(message)
        if reaction is None or not message.associated_message_guid:
            return None
        kind, removed = reaction
        target = target_guid(message.associated_message_guid)
        sender = "me" if message.is_from_me else (message.handle.address if message.handle else "?")
        entries = self._reactions.setdefault(target, {})
        previous = entries.get(sender)
        # Pages load newest first, so an older tapback must not undo a newer one.
        if previous is None or previous.at <= message.date_created:
            entries[sender] = _ReactionState(
                kind,
                removed,
                message.date_created,
                message.is_from_me,
                message.handle.address if message.handle else None,
            )
        return target

    def _reactions_for(self, guid: str) -> list[Reaction]:
        return [
            Reaction(
                type=state.type,
                from_me=state.from_me,
                sender=None if state.from_me or not state.address else self._names.person(state.address),
            )
            for state in self._reactions.get(guid, {}).values()
            if not state.removed
        ]

    async def _ingest(self, message: BBMessage, *, new: bool, chat_id: str | None = None) -> None:
        chat_id = chat_id or (
            message.chats[0].guid if message.chats else self._message_chat.get(message.guid)
        )
        if chat_id is None:
            return
        if chat_id not in self._chats:
            await self._refresh_chat(chat_id)
        target = self._apply_reaction(message)
        if target is not None:
            self._bump_last(chat_id, message)
            original = await self._lookup(target, fetch=True)
            if original is not None:
                built = await self._build(original, chat_id, fetch_budget=1)
                if built is not None:
                    await self._sink.publish(MessageUpdate(message=built, chat=self._chat_out(chat_id)))
            await self._sink.publish(ChatUpdate(chat=self._chat_out(chat_id)))
            return
        if message.is_from_me and not message.temp_guid:
            message = self._claim_echo(message, chat_id)
        known = message.guid in self._messages
        self._remember(message, chat_id)
        self._bump_last(chat_id, message)
        if new and not known and not message.is_from_me and not message.item_type:
            self._unread[chat_id] = self._unread.get(chat_id, 0) + 1
        built = await self._build(message, chat_id, fetch_budget=1)
        if built is not None:
            await self._sink.publish(MessageUpdate(message=built, chat=self._chat_out(chat_id)))
        else:
            await self._sink.publish(ChatUpdate(chat=self._chat_out(chat_id)))

    def _claim_echo(self, message: BBMessage, chat_id: str) -> BBMessage:
        """The Mac can report our send before the send call returns; tie it to that send."""
        job = self._inflight
        if job is None or job.chat_id != chat_id:
            return message
        names = {a.transfer_name for a in message.attachments}
        if (job.filename and job.filename in names) or (
            not job.filename and clean_text(message) == job.text.strip()
        ):
            return message.model_copy(update={"temp_guid": job.temp_id})
        return message

    def _bump_last(self, chat_id: str, message: BBMessage) -> None:
        chat = self._chats.get(chat_id)
        if chat is None:
            return
        last = chat.last_message
        if last is None or last.date_created <= message.date_created:
            self._chats[chat_id] = chat.model_copy(update={"last_message": message})

    async def _refresh_chat(self, chat_id: str) -> BBChat:
        chat = await self._client.chat(chat_id)
        self._chats[chat_id] = chat
        self._unread.setdefault(chat_id, 0)
        return chat

    async def _lookup(self, guid: str, *, fetch: bool) -> BBMessage | None:
        cached = self._messages.get(guid)
        if cached is not None or not fetch:
            return cached
        try:
            message = await self._client.message(guid)
        except BlueBubblesUnavailableError:
            raise
        except BlueBubblesError:
            return None
        self._remember(message, message.chats[0].guid if message.chats else None)
        return message

    async def _build(self, message: BBMessage, chat_id: str, *, fetch_budget: int) -> ChatMessage | None:
        chat = self._chats.get(chat_id)
        participants: Sequence[BBHandle] = chat.participants if chat else []
        reply: ReplyRef | None = None
        if message.thread_originator_guid:
            original = await self._lookup(message.thread_originator_guid, fetch=fetch_budget > 0)
            if original is not None:
                reply = ReplyRef(
                    id=original.guid,
                    text=preview(original, self._names, group=False),
                    sender_name="You"
                    if original.is_from_me
                    else self._names.short(original.handle.address if original.handle else None),
                )
        return message_from_bb(
            message,
            chat_id,
            self._names,
            reactions=self._reactions_for(message.guid),
            reply_to=reply,
            participants=participants,
        )

    def _chat_out(self, chat_id: str) -> Chat:
        return chat_from_bb(self._chats[chat_id], self._names, unread=self._unread.get(chat_id, 0))

    # Queries

    async def status(self) -> AppStatus:
        return self._status.model_copy(
            update={"contacts_count": len(self._contacts), "last_sync_at": self._last_sync}
        )

    async def chats(self, query: str | None = None) -> list[Chat]:
        chats = [self._chat_out(guid) for guid in self._chats]
        if query and query.strip():
            needle = query.strip().casefold()
            chats = [
                c
                for c in chats
                if needle in c.title.casefold()
                or any(
                    needle in p.address.casefold() or needle in (p.name or "").casefold()
                    for p in c.participants
                )
            ]
        return sorted(chats, key=lambda c: c.last_at or datetime.min.replace(tzinfo=UTC), reverse=True)

    async def chat(self, chat_id: str) -> Chat:
        if chat_id not in self._chats:
            try:
                await self._refresh_chat(chat_id)
            except BlueBubblesError as exc:
                raise NotFoundError(chat_id) from exc
        return self._chat_out(chat_id)

    async def messages(self, chat_id: str, *, before: str | None, limit: int) -> ThreadPage:
        chat = await self.chat(chat_id)
        before_ms = int(before) if before and before.isdigit() else None
        raw = await self._call(self._client.chat_messages(chat_id, before_ms=before_ms, limit=limit))
        ordered = sorted(raw, key=lambda m: m.date_created)
        for message in ordered:
            self._remember(message, chat_id)
            self._apply_reaction(message)
        out: list[ChatMessage] = []
        budget = _REPLY_FETCH_BUDGET
        for message in ordered:
            built = await self._build(message, chat_id, fetch_budget=budget)
            if message.thread_originator_guid:
                budget -= 1
            if built is not None:
                out.append(built)
        cursor = str(ordered[0].date_created) if len(raw) >= limit and ordered else None
        return ThreadPage(chat=chat, messages=out, cursor=cursor)

    async def search(self, query: str, limit: int) -> list[ChatSearchHit]:
        if not query.strip():
            return []
        hits: list[ChatSearchHit] = []
        for message in await self._call(self._client.search(query.strip(), limit=limit)):
            if not message.chats or reaction_of(message) is not None:
                continue
            chat_id = message.chats[0].guid
            chat = await self.chat(chat_id)
            self._remember(message, chat_id)
            built = await self._build(message, chat_id, fetch_budget=0)
            if built is not None:
                hits.append(ChatSearchHit(message=built, chat=chat))
        return hits

    async def contacts(self, query: str, limit: int) -> list[ContactCard]:
        needle = query.strip().casefold()
        cards = [
            ContactCard(name=c.name, addresses=c.addresses)
            for c in self._contacts
            if c.name
            and c.addresses
            and (
                not needle or needle in c.name.casefold() or any(needle in a.casefold() for a in c.addresses)
            )
        ]
        return sorted(cards, key=lambda c: c.name.casefold())[:limit]

    async def open_attachment(self, attachment_id: str, *, thumb: bool) -> AttachmentStream:
        response = await self._call(self._client.open_attachment(attachment_id, thumb=thumb))
        size = response.headers.get("content-length")
        return AttachmentStream(
            content_type=response.headers.get("content-type", "application/octet-stream"),
            chunks=response.aiter_bytes(),
            close=response.aclose,
            size=int(size) if size and size.isdigit() else None,
        )

    # Sending

    def _method(self) -> str:
        return "private-api" if self._private else "apple-script"

    async def _enqueue(self, job: _Outgoing) -> ChatMessage:
        self._pending[job.temp_id] = job
        message = job.placeholder(Delivery.SENDING, at=self._clock())
        await self._sink.publish(MessageUpdate(message=message, chat=await self.chat(job.chat_id)))
        self._outbox.put_nowait(job)
        return message

    async def send_text(
        self, chat_id: str, text: str, *, reply_to: str | None, temp_id: str | None
    ) -> ChatMessage:
        if not text.strip():
            raise RequestError("Type a message first.")
        if reply_to and not self._features.replies:
            raise UnsupportedError(NEEDS_PRIVATE_API)
        await self.chat(chat_id)
        return await self._enqueue(
            _Outgoing(temp_id=temp_id or uuid.uuid4().hex, chat_id=chat_id, text=text, reply_to=reply_to)
        )

    async def send_attachment(
        self, chat_id: str, *, filename: str, content_type: str, data: bytes, temp_id: str | None
    ) -> ChatMessage:
        await self.chat(chat_id)
        return await self._enqueue(
            _Outgoing(
                temp_id=temp_id or uuid.uuid4().hex,
                chat_id=chat_id,
                filename=filename,
                content_type=content_type,
                data=data,
            )
        )

    async def _send_worker(self) -> None:
        while True:
            job = await self._outbox.get()
            self._inflight = job
            try:
                if job.filename:
                    sent = await self._client.send_attachment(
                        job.chat_id,
                        temp_guid=job.temp_id,
                        filename=job.filename,
                        content=job.data,
                        content_type=job.content_type,
                        method=self._method(),
                    )
                else:
                    sent = await self._client.send_text(
                        job.chat_id,
                        job.text,
                        temp_guid=job.temp_id,
                        method="private-api" if job.reply_to else self._method(),
                        reply_to=job.reply_to,
                    )
            except BlueBubblesError as exc:
                failed = job.placeholder(Delivery.FAILED, at=self._clock(), error=str(exc))
                await self._sink.publish(MessageUpdate(message=failed, chat=self._chat_out(job.chat_id)))
                continue
            finally:
                self._inflight = None
            self._pending.pop(job.temp_id, None)
            await self._ingest(
                sent.model_copy(update={"temp_guid": job.temp_id}), new=False, chat_id=job.chat_id
            )

    async def retry(self, chat_id: str, message_id: str) -> ChatMessage:
        job = self._pending.get(message_id.removeprefix("temp:"))
        if job is None:
            raise NotFoundError(message_id)
        return await self._enqueue(job)

    async def new_chat(self, addresses: Sequence[str], text: str) -> ThreadPage:
        if len(addresses) > 1 and not self._features.new_group:
            raise UnsupportedError(NEEDS_PRIVATE_API)
        chat = await self._call(
            self._client.new_chat(addresses, text, temp_guid=uuid.uuid4().hex, method=self._method())
        )
        self._chats[chat.guid] = chat
        self._unread.setdefault(chat.guid, 0)
        await self._sink.publish(ChatUpdate(chat=self._chat_out(chat.guid)))
        return await self.messages(chat.guid, before=None, limit=50)

    # Chat actions

    def _require(self, enabled: bool) -> None:
        if not enabled:
            raise UnsupportedError(NEEDS_PRIVATE_API)

    async def mark_read(self, chat_id: str) -> None:
        if chat_id in self._chats and self._unread.get(chat_id):
            self._unread[chat_id] = 0
            await self._sink.publish(ChatUpdate(chat=self._chat_out(chat_id)))
        if self._private:
            try:
                await self._client.mark_read(chat_id)
            except BlueBubblesError as exc:
                logger.info("could not mark read on the Mac: %s", exc)

    async def set_typing(self, chat_id: str, *, typing: bool) -> None:
        if self._features.typing:
            with contextlib.suppress(BlueBubblesError):
                await self._client.set_typing(chat_id, typing=typing)

    async def _changed_chat(self, chat: BBChat) -> Chat:
        self._chats[chat.guid] = chat
        out = self._chat_out(chat.guid)
        await self._sink.publish(ChatUpdate(chat=out))
        return out

    async def rename(self, chat_id: str, title: str) -> Chat:
        self._require(self._features.manage_groups)
        await self._call(self._client.rename(chat_id, title))
        return await self._changed_chat(await self._call(self._client.chat(chat_id)))

    async def add_participant(self, chat_id: str, address: str) -> Chat:
        self._require(self._features.manage_groups)
        await self._call(self._client.add_participant(chat_id, address))
        return await self._changed_chat(await self._call(self._client.chat(chat_id)))

    async def remove_participant(self, chat_id: str, address: str) -> Chat:
        self._require(self._features.manage_groups)
        await self._call(self._client.remove_participant(chat_id, address))
        return await self._changed_chat(await self._call(self._client.chat(chat_id)))

    async def leave(self, chat_id: str) -> None:
        self._require(self._features.manage_groups)
        await self._call(self._client.leave(chat_id))
        self._chats.pop(chat_id, None)
        await self._sink.publish(ChatRemoved(chat_id=chat_id))

    async def react(self, chat_id: str, message_id: str, reaction: ReactionType, *, remove: bool) -> None:
        self._require(self._features.reactions)
        await self._call(
            self._client.react(chat_id, message_id, f"-{reaction.value}" if remove else reaction.value)
        )

    async def edit(self, chat_id: str, message_id: str, text: str) -> None:
        self._require(self._features.edit)
        await self._call(self._client.edit(message_id, text))

    async def unsend(self, chat_id: str, message_id: str) -> None:
        self._require(self._features.unsend)
        await self._call(self._client.unsend(message_id))

    # FaceTime

    async def answer_call(self, call_id: str) -> str:
        self._require(self._features.facetime)
        if call_id.startswith("legacy:"):
            raise RequestError(
                "To answer from this PC, turn on “FaceTime Calling (Experimental)” in BlueBubbles Server."
            )
        return await self._call(self._client.facetime_answer(call_id))

    async def decline_call(self, call_id: str) -> None:
        if call_id in self._placed:
            await self._end_placed_call(call_id)
            return
        if not call_id.startswith("legacy:"):
            await self._call(self._client.facetime_leave(call_id))
        await self._sink.publish(CallUpdate(call=Call(id=call_id, status=CallStatus.ENDED)))

    async def start_call(self, chat_id: str, *, video: bool) -> Call:
        """Rings the other person from the Mac; the call moves to this PC once they answer."""
        if self._calls is None or not (self._direct_calls or await self._calls.available()):
            raise UnsupportedError(NEEDS_CALL_HELPER)
        self._direct_calls = True
        chat = self._chats.get(chat_id) or await self._call(self._refresh_chat(chat_id))
        if is_group(chat.guid, chat.style) or not chat.participants:
            raise RequestError("Group FaceTime calls aren't supported yet. Call one person at a time.")
        person = self._names.person(chat.participants[0].address)
        try:
            placed = await self._calls.dial(person.address, video=video)
        except CallHelperError as exc:
            raise RequestError(str(exc)) from exc
        call = self._placed_call(placed, person, video=video)
        self._placed[placed.id] = _PlacedCall(call, link=placed.link)
        self._placed_changed.set()
        await self._sink.publish(CallUpdate(call=call))
        return call

    async def join_call(self, call_id: str) -> str:
        placed = self._placed.get(call_id)
        if placed is None:
            raise NotFoundError(call_id)
        if not placed.link:
            raise RequestError("The call isn't ready for this PC yet.")
        return placed.link

    @staticmethod
    def _placed_call(state: HelperCall, person: Person | None, *, video: bool) -> Call:
        return Call(
            id=state.id,
            status=_CALL_PHASES[state.phase],
            caller=person,
            is_video=video,
            outgoing=True,
            detail=state.reason,
        )

    async def _call_worker(self) -> None:
        while True:
            if not self._placed:
                await self._placed_changed.wait()
                self._placed_changed.clear()
                continue
            await asyncio.sleep(self._call_poll)
            for call_id in list(self._placed):
                await self._poll_placed_call(call_id)

    async def _poll_placed_call(self, call_id: str) -> None:
        placed = self._placed.get(call_id)
        if placed is None or self._calls is None:
            return
        try:
            state = await self._calls.call(call_id)
        except CallHelperUnavailableError:
            placed.misses += 1
            if placed.misses >= _CALL_MISSES:
                await self._finish_placed_call(call_id, "Lost contact with the Mac.")
            return
        except CallHelperError as exc:
            await self._finish_placed_call(call_id, str(exc))
            return
        placed.misses = 0
        placed.link = state.link or placed.link
        updated = self._placed_call(state, placed.call.caller, video=placed.call.is_video)
        if updated.status in (CallStatus.CONNECTED, CallStatus.ENDED):
            del self._placed[call_id]
        if updated != placed.call:
            placed.call = updated
            await self._sink.publish(CallUpdate(call=updated))

    async def _end_placed_call(self, call_id: str) -> None:
        if self._calls is None:
            return
        try:
            await self._calls.end(call_id)
        except CallHelperError as exc:
            raise RequestError(str(exc)) from exc
        await self._finish_placed_call(call_id, None)

    async def _finish_placed_call(self, call_id: str, detail: str | None) -> None:
        placed = self._placed.pop(call_id, None)
        if placed is not None:
            ended = placed.call.model_copy(update={"status": CallStatus.ENDED, "detail": detail})
            await self._sink.publish(CallUpdate(call=ended))

    # Contacts

    async def photo(self, photo_id: str) -> Photo:
        image = self._names.photo(photo_id)
        if image is None:
            raise NotFoundError(photo_id)
        return Photo(content_type=_image_type(image), data=image)

    async def close_phone_link(self) -> bool:
        return False

    async def _call[T](self, operation: Awaitable[T]) -> T:
        try:
            return await operation
        except BlueBubblesError as exc:
            raise RequestError(str(exc)) from exc
