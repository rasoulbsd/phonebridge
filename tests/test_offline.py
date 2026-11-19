from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
import pytest
from pydantic import SecretStr

from phonebridge.config import ConfigStore, MacConnection, UserConfig
from phonebridge.domain.ui import Chat, ChatMessage, Delivery, MessageUpdate, State, StatusUpdate
from phonebridge.mac.client import BlueBubblesClient
from phonebridge.services.mac_service import MacService
from phonebridge.services.messaging import NotFoundError, Photo
from phonebridge.services.offline import SAVED_NOTE, OfflineMacService
from phonebridge.storage.mac_cache import MacCache
from tests.conftest import UpdateRecorder
from tests.fake_bluebubbles import BASE, DM, GROUP, MAYA, FakeBlueBubbles, FakeEvents

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable
    from pathlib import Path

    from phonebridge.domain.ui import UiUpdate
    from phonebridge.services.messaging import UiSink


def unreachable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("Mac is asleep", request=request)


@contextlib.asynccontextmanager
async def open_cache(tmp_path: Path) -> AsyncGenerator[MacCache]:
    cache = await MacCache.open(tmp_path / "mac-cache.db", tmp_path / "attachments")
    try:
        yield cache
    finally:
        await cache.close()


@contextlib.asynccontextmanager
async def running_offline(
    server: FakeBlueBubbles, cache: MacCache, feed: FakeEvents | None = None
) -> AsyncGenerator[tuple[OfflineMacService, UpdateRecorder, FakeEvents]]:
    events = feed or FakeEvents()
    sink = UpdateRecorder()

    def build(inner_sink: UiSink) -> MacService:
        client = BlueBubblesClient("http://mac.test:1234", "pw", transport=server.transport())
        return MacService(client, lambda: events, inner_sink, refresh_every=60.0)

    service = OfflineMacService(build, cache, sink)
    task = asyncio.create_task(service.run())
    try:
        yield service, sink, events
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await service.close()


def is_state(state: State) -> Callable[[UiUpdate], bool]:
    def check(update: UiUpdate) -> bool:
        return isinstance(update, StatusUpdate) and update.status.state is state

    return check


async def test_messages_survive_a_restart_while_the_mac_is_unreachable(tmp_path: Path) -> None:
    async with open_cache(tmp_path) as cache:
        async with running_offline(FakeBlueBubbles(), cache) as (service, _, feed):
            await asyncio.wait_for(service.synced.wait(), 3)
            feed.push(
                "new-message",
                {
                    "guid": "live1",
                    "text": "Arrived while connected",
                    "handle": MAYA,
                    "dateCreated": BASE + 30 * 60_000,
                    "chats": [{"guid": DM}],
                },
            )
            await asyncio.sleep(0.2)
            photo = await service.open_attachment("att1", thumb=True)
            online_bytes = b"".join([chunk async for chunk in photo.chunks])
            await photo.close()

        asleep = FakeBlueBubbles()
        asleep.override = unreachable
        async with running_offline(asleep, cache) as (service, sink, _):
            down = await sink.wait_for(is_state(State.DISCONNECTED))
            chats = await service.chats()
            group = await service.messages(GROUP, before=None, limit=50)
            dm = await service.messages(DM, before=None, limit=50)
            hits = await service.search("saturday", 10)
            contacts = await service.contacts("maya", 10)
            saved_photo = await service.open_attachment("att1", thumb=True)
            offline_bytes = b"".join([chunk async for chunk in saved_photo.chunks])

    assert isinstance(down, StatusUpdate)
    assert down.status.detail is not None
    assert down.status.detail.endswith(SAVED_NOTE)
    # Last session's features stay on, so saved photos and reactions still show.
    assert (down.status.features.attachments, down.status.features.reactions) == (True, True)
    assert down.status.device_name == "Test-MacBook"
    assert {c.id for c in chats} == {GROUP, DM}
    assert [m.id for m in group.messages] == ["g1", "g2", "g4", "g5", "g6"]
    assert group.messages[0].reactions  # tapbacks are kept with the message
    assert [m.text for m in dm.messages][-1] == "Arrived while connected"
    assert [(h.message.id, h.chat.id) for h in hits] == [("g1", GROUP)]
    assert [c.name for c in contacts] == ["Maya Chen"]
    assert offline_bytes == online_bytes == b"\xff\xd8jpeg"


async def test_offline_pages_use_the_same_cursor_as_the_mac(tmp_path: Path) -> None:
    async with open_cache(tmp_path) as cache:
        async with running_offline(FakeBlueBubbles(), cache) as (service, _, _):
            await asyncio.wait_for(service.synced.wait(), 3)
        asleep = FakeBlueBubbles()
        asleep.override = unreachable
        async with running_offline(asleep, cache) as (service, sink, _):
            await sink.wait_for(is_state(State.DISCONNECTED))
            first = await service.messages(GROUP, before=None, limit=2)
            older = await service.messages(GROUP, before=first.cursor, limit=50)
    assert [m.id for m in first.messages] == ["g5", "g6"]
    assert first.cursor == str(BASE + 5 * 60_000)
    assert ([m.id for m in older.messages], older.cursor) == (["g1", "g2", "g4"], None)


async def test_unknown_chat_offline_is_not_found(tmp_path: Path) -> None:
    asleep = FakeBlueBubbles()
    asleep.override = unreachable
    async with open_cache(tmp_path) as cache, running_offline(asleep, cache) as (service, sink, _):
        await sink.wait_for(is_state(State.DISCONNECTED))
        with pytest.raises(NotFoundError):
            await service.messages("iMessage;-;nobody", before=None, limit=10)
        assert await service.chats() == []


async def test_temporary_sends_are_not_saved(tmp_path: Path) -> None:
    async with open_cache(tmp_path) as cache:
        chat = Chat(id=DM, title="Maya")
        pending = ChatMessage(
            id="temp:t1",
            chat_id=DM,
            from_me=True,
            text="hi",
            at=datetime(2026, 11, 18, tzinfo=UTC),
            status=Delivery.SENDING,
        )
        await cache.upsert_chats([chat])
        await cache.upsert_messages([pending])
        assert await cache.message_count() == 0
        assert MessageUpdate(message=pending, chat=chat).message.id.startswith("temp:")


async def test_switching_macs_clears_the_copy(tmp_path: Path) -> None:
    async with open_cache(tmp_path) as cache:
        assert not await cache.use_device("Mac A")
        await cache.upsert_chats([Chat(id=DM, title="Maya")])
        await cache.save_photo("p1", Photo(content_type="image/jpeg", data=b"x"))
        assert not await cache.use_device("Mac A")
        assert await cache.use_device("Mac B")
        assert (await cache.chats(), await cache.photo("p1")) == ([], None)


class MemoryBackup:
    def __init__(self) -> None:
        self.text: str | None = None

    def read(self) -> str | None:
        return self.text

    def write(self, text: str) -> None:
        self.text = text


def test_settings_backup_restores_a_missing_or_damaged_file(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    backup = MemoryBackup()
    store = ConfigStore(path, backup)
    store.save(UserConfig(mac=MacConnection(url="http://mac:1234", password=SecretStr("s3cret"))))
    assert backup.text is not None
    assert "s3cret" not in backup.text

    path.unlink()
    restored = store.load()
    assert restored.mac is not None
    assert (restored.mac.url, restored.mac.password.get_secret_value()) == ("http://mac:1234", "s3cret")
    assert path.exists()

    path.write_text("{not json", encoding="utf-8")
    again = store.load()
    assert again.mac is not None
    assert again.mac.url == "http://mac:1234"


def test_loading_a_good_file_refreshes_a_missing_backup(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    ConfigStore(path).save(UserConfig(mac=MacConnection(url="http://mac:1234", password=SecretStr("pw"))))
    backup = MemoryBackup()
    ConfigStore(path, backup).load()
    assert backup.text == path.read_text(encoding="utf-8")


def test_without_backup_a_missing_file_means_no_mac(tmp_path: Path) -> None:
    assert ConfigStore(tmp_path / "config.json").load() == UserConfig()
