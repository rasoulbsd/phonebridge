"""Mac mode with a local copy.

Everything the Mac shares is saved as it arrives, and each time the Mac connects the newest
messages of every chat are fetched, so the copy also covers what arrived while the PC was off.
While the Mac is asleep, off or unreachable, chats, conversations, search, contacts, photos
and already-opened attachments come from the copy instead of disappearing.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, BinaryIO

from phonebridge.domain.ui import ChatRemoved, ChatUpdate, MessageUpdate, State, StatusUpdate, ThreadPage
from phonebridge.services.messaging import AttachmentStream, NotFoundError, RequestError
from phonebridge.storage.mac_cache import at_ms, file_key

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Sequence
    from pathlib import Path

    from phonebridge.domain.ui import (
        AppStatus,
        Call,
        Chat,
        ChatMessage,
        ChatSearchHit,
        ContactCard,
        ReactionType,
        UiUpdate,
    )
    from phonebridge.services.messaging import MessagingService, Photo, UiSink
    from phonebridge.storage.mac_cache import MacCache

logger = logging.getLogger(__name__)

SAVED_NOTE = "Showing messages saved on this PC."
_BACKFILL_PAGE = 100
# Pause between chats while backfilling, so the Mac isn't flooded with requests.
_BACKFILL_PAUSE = 0.2
_MAX_SAVED_FILE = 50 * 1024 * 1024
_READ_CHUNK = 64 * 1024
_ALL_CONTACTS = 100_000


def _open_for_reading(path: Path) -> BinaryIO:
    return path.open("rb")


def _open_for_writing(path: Path) -> BinaryIO:
    return path.open("wb")


async def _read_file(path: Path) -> AsyncIterator[bytes]:
    handle = await asyncio.to_thread(_open_for_reading, path)
    try:
        while chunk := await asyncio.to_thread(handle.read, _READ_CHUNK):
            yield chunk
    finally:
        await asyncio.to_thread(handle.close)


async def _nothing() -> None:
    return None


class _SavingSink:
    """Saves each update the Mac service publishes, then passes it on."""

    def __init__(self, owner: OfflineMacService) -> None:
        self._owner = owner

    async def publish(self, update: UiUpdate) -> None:
        await self._owner.on_update(update)


class OfflineMacService:
    def __init__(
        self,
        build: Callable[[UiSink], MessagingService],
        cache: MacCache,
        sink: UiSink,
    ) -> None:
        self._cache = cache
        self._sink = sink
        self._online = False
        self._has_saved = False
        self._last_known: AppStatus | None = None
        self._connected = asyncio.Event()
        # Set once the copy has caught up with the Mac after it connects.
        self.synced = asyncio.Event()
        self._inner = build(_SavingSink(self))

    # Lifecycle

    async def run(self) -> None:
        self._has_saved = await self._cache.message_count() > 0
        self._last_known = await self._cache.last_status()
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._inner.run())
            tg.create_task(self._backfill_forever())

    async def close(self) -> None:
        await self._inner.close()

    async def on_update(self, update: UiUpdate) -> None:
        match update:
            case MessageUpdate():
                await self._cache.upsert_messages([update.message])
                await self._cache.upsert_chats([update.chat])
                self._has_saved = self._has_saved or not update.message.id.startswith("temp:")
            case ChatUpdate():
                await self._cache.upsert_chats([update.chat])
            case ChatRemoved():
                await self._cache.delete_chat(update.chat_id)
            case StatusUpdate():
                was_online = self._online
                self._online = update.status.state is State.CONNECTED
                if self._online and not was_online:
                    self._connected.set()
                if self._online:
                    self._last_known = update.status
                    await self._cache.save_last_status(update.status)
                update = StatusUpdate(status=self._decorate(update.status))
            case _:
                pass
        await self._sink.publish(update)

    def _decorate(self, status: AppStatus) -> AppStatus:
        if status.state is State.CONNECTED or not self._has_saved:
            return status
        detail = f"{status.detail} {SAVED_NOTE}" if status.detail else SAVED_NOTE
        changes: dict[str, object] = {"detail": detail}
        last = self._last_known
        if last is not None:
            # Before the Mac answers, keep what it supported last time so saved photos,
            # reactions and groups still display; sending is off while disconnected anyway.
            changes["features"] = last.features
            changes["device_name"] = status.device_name or last.device_name
            if status.mac is None or status.mac.server_version is None:
                changes["mac"] = last.mac
        return status.model_copy(update=changes)

    # Backfill

    async def _backfill_forever(self) -> None:
        while True:
            await self._connected.wait()
            self._connected.clear()
            try:
                await self._backfill()
            except (RequestError, NotFoundError) as exc:
                logger.info("saving messages paused: %s", exc)

    async def _backfill(self) -> None:
        status = await self._inner.status()
        device = status.device_name or (status.mac.url if status.mac else "mac")
        if await self._cache.use_device(device):
            logger.info("a different Mac is connected; cleared the saved messages")
        chats = await self._inner.chats()
        await self._cache.upsert_chats(chats)
        await self._cache.save_contacts(await self._inner.contacts("", _ALL_CONTACTS))
        fetched = 0
        for chat in chats:
            if not self._online:
                return
            saved_until = await self._cache.latest_ms(chat.id)
            if chat.last_at is not None and saved_until is not None and saved_until >= at_ms(chat.last_at):
                continue
            page = await self._inner.messages(chat.id, before=None, limit=_BACKFILL_PAGE)
            await self._cache.upsert_messages(page.messages)
            self._has_saved = self._has_saved or bool(page.messages)
            fetched += 1
            await asyncio.sleep(_BACKFILL_PAUSE)
        logger.info("saved messages are up to date (%d chats refreshed)", fetched)
        self.synced.set()

    # Reads: live when the Mac is connected, from the copy otherwise

    async def status(self) -> AppStatus:
        return self._decorate(await self._inner.status())

    async def chats(self, query: str | None = None) -> list[Chat]:
        if self._online:
            chats = await self._inner.chats(query)
            if not query:
                await self._cache.upsert_chats(chats)
            return chats
        return await self._cache.chats(query)

    async def chat(self, chat_id: str) -> Chat:
        if self._online:
            with contextlib.suppress(NotFoundError, RequestError):
                chat = await self._inner.chat(chat_id)
                await self._cache.upsert_chats([chat])
                return chat
        saved = await self._cache.chat(chat_id)
        if saved is None:
            raise NotFoundError(chat_id)
        return saved

    async def messages(self, chat_id: str, *, before: str | None, limit: int) -> ThreadPage:
        if self._online:
            try:
                page = await self._inner.messages(chat_id, before=before, limit=limit)
            except RequestError as exc:
                logger.info("reading saved messages instead: %s", exc)
            else:
                await self._cache.upsert_chats([page.chat])
                await self._cache.upsert_messages(page.messages)
                return page
        chat = await self.chat(chat_id)
        before_ms = int(before) if before and before.isdigit() else None
        messages, cursor = await self._cache.page(chat_id, before_ms=before_ms, limit=limit)
        return ThreadPage(chat=chat, messages=messages, cursor=cursor)

    async def search(self, query: str, limit: int) -> list[ChatSearchHit]:
        if self._online:
            try:
                hits = await self._inner.search(query, limit)
            except RequestError as exc:
                logger.info("searching saved messages instead: %s", exc)
            else:
                await self._cache.upsert_messages([hit.message for hit in hits])
                return hits
        return await self._cache.search(query, limit)

    async def contacts(self, query: str, limit: int) -> list[ContactCard]:
        if self._online:
            return await self._inner.contacts(query, limit)
        return await self._cache.contacts(query, limit)

    async def photo(self, photo_id: str) -> Photo:
        try:
            photo = await self._inner.photo(photo_id)
        except NotFoundError:
            saved = await self._cache.photo(photo_id)
            if saved is None:
                raise
            return saved
        await self._cache.save_photo(photo_id, photo)
        return photo

    async def open_attachment(self, attachment_id: str, *, thumb: bool) -> AttachmentStream:
        saved = await self._cache.file(attachment_id, thumb=thumb)
        if saved is not None:
            path, content_type, size = saved
            return AttachmentStream(
                content_type=content_type, chunks=_read_file(path), close=_nothing, size=size
            )
        stream = await self._inner.open_attachment(attachment_id, thumb=thumb)
        if stream.size is not None and stream.size > _MAX_SAVED_FILE:
            return stream
        return AttachmentStream(
            content_type=stream.content_type,
            chunks=self._save_while_streaming(stream, attachment_id, thumb=thumb),
            close=stream.close,
            size=stream.size,
        )

    async def _save_while_streaming(
        self, stream: AttachmentStream, attachment_id: str, *, thumb: bool
    ) -> AsyncIterator[bytes]:
        final = self._cache.files_dir / file_key(attachment_id, thumb=thumb)
        partial = final.with_suffix(".part")
        handle: BinaryIO | None = await asyncio.to_thread(_open_for_writing, partial)
        size = 0
        try:
            async for chunk in stream.chunks:
                if handle is not None:
                    size += len(chunk)
                    if size > _MAX_SAVED_FILE:
                        await asyncio.to_thread(handle.close)
                        handle = None
                    else:
                        await asyncio.to_thread(handle.write, chunk)
                yield chunk
            if handle is not None:
                await asyncio.to_thread(handle.close)
                handle = None
                await asyncio.to_thread(partial.replace, final)
                await self._cache.remember_file(
                    attachment_id, thumb=thumb, content_type=stream.content_type, size=size
                )
        finally:
            if handle is not None:
                await asyncio.to_thread(handle.close)
            with contextlib.suppress(OSError):
                await asyncio.to_thread(partial.unlink, missing_ok=True)

    # Everything else goes to the Mac

    async def send_text(
        self, chat_id: str, text: str, *, reply_to: str | None, temp_id: str | None
    ) -> ChatMessage:
        return await self._inner.send_text(chat_id, text, reply_to=reply_to, temp_id=temp_id)

    async def send_attachment(
        self, chat_id: str, *, filename: str, content_type: str, data: bytes, temp_id: str | None
    ) -> ChatMessage:
        return await self._inner.send_attachment(
            chat_id, filename=filename, content_type=content_type, data=data, temp_id=temp_id
        )

    async def new_chat(self, addresses: Sequence[str], text: str) -> ThreadPage:
        return await self._inner.new_chat(addresses, text)

    async def mark_read(self, chat_id: str) -> None:
        await self._inner.mark_read(chat_id)

    async def set_typing(self, chat_id: str, *, typing: bool) -> None:
        await self._inner.set_typing(chat_id, typing=typing)

    async def rename(self, chat_id: str, title: str) -> Chat:
        return await self._inner.rename(chat_id, title)

    async def add_participant(self, chat_id: str, address: str) -> Chat:
        return await self._inner.add_participant(chat_id, address)

    async def remove_participant(self, chat_id: str, address: str) -> Chat:
        return await self._inner.remove_participant(chat_id, address)

    async def leave(self, chat_id: str) -> None:
        await self._inner.leave(chat_id)

    async def react(self, chat_id: str, message_id: str, reaction: ReactionType, *, remove: bool) -> None:
        await self._inner.react(chat_id, message_id, reaction, remove=remove)

    async def edit(self, chat_id: str, message_id: str, text: str) -> None:
        await self._inner.edit(chat_id, message_id, text)

    async def unsend(self, chat_id: str, message_id: str) -> None:
        await self._inner.unsend(chat_id, message_id)

    async def retry(self, chat_id: str, message_id: str) -> ChatMessage:
        return await self._inner.retry(chat_id, message_id)

    async def answer_call(self, call_id: str) -> str:
        return await self._inner.answer_call(call_id)

    async def decline_call(self, call_id: str) -> None:
        await self._inner.decline_call(call_id)

    async def start_call(self, chat_id: str, *, video: bool) -> Call:
        return await self._inner.start_call(chat_id, video=video)

    async def join_call(self, call_id: str) -> str:
        return await self._inner.join_call(call_id)

    async def close_phone_link(self) -> bool:
        return await self._inner.close_phone_link()
