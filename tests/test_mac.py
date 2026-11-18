from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import logging
from typing import TYPE_CHECKING

import httpx
import pytest
from PIL import Image
from pydantic import SecretStr

from phonebridge.config import ConfigStore, MacConnection, UserConfig
from phonebridge.domain.ui import (
    CallStatus,
    CallUpdate,
    ChatUpdate,
    Delivery,
    MessageUpdate,
    Mode,
    Person,
    ReactionType,
    State,
    StatusUpdate,
    TypingUpdate,
)
from phonebridge.mac.call_helper import CallPhase
from phonebridge.mac.client import BlueBubblesClient
from phonebridge.mac.models import BBContact, BBMessage, BBServerInfo
from phonebridge.mac.photos import thumbnail
from phonebridge.mac.translate import Names, reaction_of, send_error, target_guid
from phonebridge.runtime import quiet_http_logs
from phonebridge.services.mac_service import MacService, features_for
from phonebridge.services.manager import ServiceManager, normalize_mac_url
from phonebridge.services.messaging import NotFoundError, Photo, RequestError, UnsupportedError
from phonebridge.services.phone_service import PhoneService
from tests.conftest import FakeConnector, UpdateRecorder
from tests.fake_bluebubbles import BASE, DM, GROUP, MAYA, SAM, FakeBlueBubbles, FakeEvents, message
from tests.fake_call_helper import LINK, FakeCallHelper

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable
    from pathlib import Path

    from phonebridge.domain.ui import UiUpdate
    from phonebridge.services.messaging import MessagingService
    from phonebridge.storage import SqliteRepository


def connected(update: UiUpdate) -> bool:
    return isinstance(update, StatusUpdate) and update.status.state is State.CONNECTED


@contextlib.asynccontextmanager
async def running_mac(
    server: FakeBlueBubbles,
    events: FakeEvents | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    refresh_every: float = 60.0,
    calls: FakeCallHelper | None = None,
) -> AsyncGenerator[tuple[MacService, UpdateRecorder, FakeEvents]]:
    feed = events or FakeEvents()
    sink = UpdateRecorder()
    service = MacService(
        BlueBubblesClient("http://mac.test:1234", "pw", transport=transport or server.transport()),
        lambda: feed,
        sink,
        refresh_every=refresh_every,
        calls=calls,
        call_poll=0.01,
    )
    task = asyncio.create_task(service.run())
    try:
        yield service, sink, feed
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await service.close()


def bb(guid: str, **fields: object) -> BBMessage:
    return BBMessage.model_validate({"guid": guid, **fields})


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("love", (ReactionType.LOVE, False)),
        ("-laugh", (ReactionType.LAUGH, True)),
        ("sticker", None),
        ("2006", None),
    ],
)
def test_reaction_of(kind: str, expected: tuple[ReactionType, bool] | None) -> None:
    assert reaction_of(bb("r", associatedMessageGuid="p:0/x", associatedMessageType=kind)) == expected


@pytest.mark.parametrize(("raw", "guid"), [("p:0/ABC", "ABC"), ("bp:ABC", "ABC"), ("ABC", "ABC")])
def test_target_guid(raw: str, guid: str) -> None:
    assert target_guid(raw) == guid


_TEXT_NOT_RELAYED = (
    "Not sent as a text message. Texts go through your iPhone: check it's on and has "
    "Text Message Forwarding turned on for this Mac (Settings > Apps > Messages)."
)


@pytest.mark.parametrize(
    ("fields", "chat_id", "expected"),
    [
        ({"isFromMe": False, "error": 22}, DM, None),
        ({"isFromMe": True}, DM, None),
        ({"isFromMe": True, "error": 22, "handle": MAYA}, DM, "Not delivered: they don't use iMessage."),
        ({"isFromMe": True, "error": 4, "handle": {**MAYA, "service": "SMS"}}, DM, _TEXT_NOT_RELAYED),
        ({"isFromMe": True, "error": 4}, "SMS;-;+15550142", _TEXT_NOT_RELAYED),
        ({"isFromMe": True, "error": 39}, GROUP, "Not delivered (error 39)"),
    ],
)
def test_send_error_says_why_in_plain_words(
    fields: dict[str, object], chat_id: str, expected: str | None
) -> None:
    assert send_error(bb("m", **fields), chat_id) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("192.168.1.20", "http://192.168.1.20:1234"), ("http://mac.local:1234/", "http://mac.local:1234")],
)
def test_normalize_mac_url(raw: str, expected: str) -> None:
    assert normalize_mac_url(raw) == expected


@pytest.mark.parametrize("raw", ["", "ftp://x", "http://"])
def test_normalize_mac_url_rejects_bad_input(raw: str) -> None:
    with pytest.raises(RequestError):
        normalize_mac_url(raw)


def test_features_depend_on_private_api_and_macos() -> None:
    server = FakeBlueBubbles(private_api=False)
    basic = features_for(BBServerInfo.model_validate(server.info))
    assert (basic.groups, basic.attachments, basic.reactions, basic.manage_groups) == (
        True,
        True,
        False,
        False,
    )
    full = features_for(
        BBServerInfo.model_validate({**server.info, "private_api": True, "helper_connected": True})
    )
    assert (full.reactions, full.edit, full.facetime) == (True, True, True)


def test_names_match_any_phone_format() -> None:
    names = Names([])
    assert names.name("+15550142") is None
    assert names.short("+15550142") == "+15550142"


async def test_sync_builds_chats_with_names_and_unread() -> None:
    async with running_mac(FakeBlueBubbles()) as (service, sink, _):
        await sink.wait_for(connected)
        chats = {c.id: c for c in await service.chats()}
        status = await service.status()
    group, dm = chats[GROUP], chats[DM]
    assert (dm.title, dm.unread, dm.is_group, dm.service) == ("Maya Chen", 1, False, "iMessage")
    assert group.title == "Maya, Sam and jordan@example.com"
    assert (group.last_text, group.last_sender) == ("Photo", Person(address="+15550142", name="Maya Chen"))
    assert (status.mode, status.device_name, status.contacts_count) == (Mode.MAC, "Test-MacBook", 2)
    assert status.features.reactions


async def test_thread_folds_reactions_replies_events_and_attachments() -> None:
    async with running_mac(FakeBlueBubbles()) as (service, sink, _):
        await sink.wait_for(connected)
        page = await service.messages(GROUP, before=None, limit=50)
    by_id = {m.id: m for m in page.messages}
    assert [m.id for m in page.messages] == ["g1", "g2", "g4", "g5", "g6"]
    assert [(r.type, r.sender.name if r.sender else None) for r in by_id["g1"].reactions] == [
        (ReactionType.LOVE, "Sam Okafor")
    ]
    reply = by_id["g2"].reply_to
    assert reply is not None
    assert (reply.id, reply.text, reply.sender_name) == ("g1", "Who's in for Saturday?", "Maya")
    assert by_id["g4"].event == "jordan@example.com named the conversation “Trip”"
    assert (by_id["g5"].unsent, by_id["g5"].text) == (True, "")
    [photo] = by_id["g6"].attachments
    assert (photo.url, photo.thumb_url, by_id["g6"].text) == (
        "/api/attachments/att1",
        "/api/attachments/att1?thumb=1",
        "",
    )
    assert page.cursor is None


async def test_paging_returns_cursor_and_older_messages() -> None:
    async with running_mac(FakeBlueBubbles()) as (service, sink, _):
        await sink.wait_for(connected)
        first = await service.messages(GROUP, before=None, limit=2)
        assert first.cursor == str(BASE + 5 * 60_000)
        older = await service.messages(GROUP, before=first.cursor, limit=50)
    assert [m.id for m in first.messages] == ["g5", "g6"]
    assert [m.id for m in older.messages] == ["g1", "g2", "g4"]


async def test_live_message_and_reaction_events() -> None:
    async with running_mac(FakeBlueBubbles()) as (service, sink, feed):
        await sink.wait_for(connected)
        await service.messages(DM, before=None, limit=50)
        feed.push(
            "new-message",
            {
                "guid": "d2",
                "text": "On my way",
                "handle": MAYA,
                "dateCreated": BASE + 9 * 60_000,
                "chats": [{"guid": DM}],
            },
        )
        update = await sink.wait_for(lambda u: isinstance(u, MessageUpdate) and u.message.id == "d2")
        assert isinstance(update, MessageUpdate)
        assert (update.chat.unread, update.chat.last_text) == (2, "On my way")
        feed.push(
            "new-message",
            {
                "guid": "r1",
                "isFromMe": True,
                "dateCreated": BASE + 10 * 60_000,
                "associatedMessageGuid": "p:0/d2",
                "associatedMessageType": "like",
                "chats": [{"guid": DM}],
            },
        )
        liked = await sink.wait_for(
            lambda u: isinstance(u, MessageUpdate) and u.message.id == "d2" and bool(u.message.reactions)
        )
        assert isinstance(liked, MessageUpdate)
        assert [(r.type, r.from_me) for r in liked.message.reactions] == [(ReactionType.LIKE, True)]
        feed.push("typing-indicator", {"display": True, "guid": DM})
        typing = await sink.wait_for(lambda u: isinstance(u, TypingUpdate))
        assert typing == TypingUpdate(chat_id=DM, typing=True)


async def test_send_is_optimistic_then_confirmed() -> None:
    server = FakeBlueBubbles()
    async with running_mac(server) as (service, sink, _):
        await sink.wait_for(connected)
        pending = await service.send_text(DM, "Running late", reply_to=None, temp_id="t1")
        confirmed = await sink.wait_for(
            lambda u: isinstance(u, MessageUpdate) and u.message.temp_id == "t1" and u.message.id != "temp:t1"
        )
    assert (pending.id, pending.status) == ("temp:t1", Delivery.SENDING)
    assert isinstance(confirmed, MessageUpdate)
    assert (confirmed.message.id, confirmed.message.text, confirmed.message.from_me) == (
        "sent1",
        "Running late",
        True,
    )
    assert (
        "POST",
        "/message/text",
        {"chatGuid": DM, "tempGuid": "t1", "message": "Running late", "method": "private-api"},
    ) in server.requests


async def test_failed_send_can_be_retried() -> None:
    server = FakeBlueBubbles()
    server.fail_send = True
    async with running_mac(server) as (service, sink, _):
        await sink.wait_for(connected)
        await service.send_text(DM, "hello", reply_to=None, temp_id="t2")
        failed = await sink.wait_for(
            lambda u: isinstance(u, MessageUpdate) and u.message.status is Delivery.FAILED
        )
        assert isinstance(failed, MessageUpdate)
        assert failed.message.error == "Message failed to send"
        server.fail_send = False
        await service.retry(DM, "temp:t2")
        await sink.wait_for(lambda u: isinstance(u, MessageUpdate) and u.message.id == "sent1")


async def test_private_api_features_refuse_without_it() -> None:
    async with running_mac(FakeBlueBubbles(private_api=False)) as (service, sink, _):
        await sink.wait_for(connected)
        with pytest.raises(UnsupportedError):
            await service.react(DM, "d1", ReactionType.LOVE, remove=False)
        with pytest.raises(UnsupportedError):
            await service.send_text(DM, "x", reply_to="d1", temp_id=None)


async def test_mark_read_clears_unread_and_tells_the_mac() -> None:
    server = FakeBlueBubbles()
    async with running_mac(server) as (service, sink, _):
        await sink.wait_for(connected)
        await service.mark_read(DM)
        cleared = await sink.wait_for(lambda u: isinstance(u, ChatUpdate) and u.chat.id == DM)
    assert isinstance(cleared, ChatUpdate)
    assert cleared.chat.unread == 0
    assert ("POST", f"/chat/{DM}/read", None) in server.requests


async def test_wrong_password_reports_auth_failed() -> None:
    server = FakeBlueBubbles()
    server.password = "other"
    async with running_mac(server) as (_, sink, _):
        update = await sink.wait_for(
            lambda u: isinstance(u, StatusUpdate) and u.status.state is State.AUTH_FAILED
        )
    assert isinstance(update, StatusUpdate)
    assert update.status.detail == "The Mac rejected the password. Update it in Settings."


async def test_incoming_facetime_call_and_answer() -> None:
    async with running_mac(FakeBlueBubbles()) as (service, sink, feed):
        await sink.wait_for(connected)
        feed.push(
            "ft-call-status-changed",
            {"uuid": "call-1", "status": "incoming", "address": "+15550142", "is_video": True},
        )
        update = await sink.wait_for(lambda u: isinstance(u, CallUpdate))
        link = await service.answer_call("call-1")
    assert isinstance(update, CallUpdate)
    assert update.call.status is CallStatus.INCOMING
    assert update.call.caller is not None
    assert update.call.caller.name == "Maya Chen"
    assert link == "https://facetime.apple.com/join#v=1&p=abc"


async def test_attachment_streams_from_the_mac() -> None:
    async with running_mac(FakeBlueBubbles()) as (service, sink, _):
        await sink.wait_for(connected)
        stream = await service.open_attachment("att1", thumb=True)
        data = b"".join([chunk async for chunk in stream.chunks])
        await stream.close()
    assert (stream.content_type, data) == ("image/jpeg", b"\xff\xd8jpeg")


async def test_search_returns_hits_with_chats() -> None:
    async with running_mac(FakeBlueBubbles()) as (service, sink, _):
        await sink.wait_for(connected)
        hits = await service.search("saturday", 10)
    assert [(h.message.id, h.chat.id) for h in hits] == [("g1", GROUP)]


def test_config_store_round_trip_encrypts_password(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    assert store.load() == UserConfig()
    store.save(UserConfig(mac=MacConnection(url="http://mac:1234", password=SecretStr("s3cret"))))
    assert "s3cret" not in (tmp_path / "config.json").read_text()
    loaded = store.load()
    assert loaded.mac is not None
    assert (loaded.mac.url, loaded.mac.password.get_secret_value()) == ("http://mac:1234", "s3cret")


async def test_manager_connects_and_disconnects_mac(tmp_path: Path, repo: SqliteRepository) -> None:
    server = FakeBlueBubbles()
    sink = UpdateRecorder()
    built: list[MessagingService] = []

    def factory(config: UserConfig) -> MessagingService:
        service: MessagingService
        if config.mac is None:
            service = PhoneService(FakeConnector(), repo, sink)
        else:
            client = BlueBubblesClient(
                config.mac.url, config.mac.password.get_secret_value(), transport=server.transport()
            )
            service = MacService(client, FakeEvents, sink)
        built.append(service)
        return service

    manager = ServiceManager(
        ConfigStore(tmp_path / "c.json"), factory, sink, probe_transport=server.transport()
    )
    await manager.start()
    task = asyncio.create_task(manager.run())
    try:
        with pytest.raises(RequestError, match="rejected"):
            await manager.connect_mac("mac.test", "wrong")
        status = await manager.connect_mac("mac.test", "pw")
        assert status.mode is Mode.MAC
        assert manager.config.mac is not None
        assert manager.config.mac.url == "http://mac.test:1234"
        assert (await manager.disconnect_mac()).mode is Mode.PHONE
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    assert [type(s).__name__ for s in built] == ["PhoneService", "MacService", "PhoneService"]


async def test_password_never_reaches_logs(caplog: pytest.LogCaptureFixture) -> None:
    quiet_http_logs()
    caplog.set_level(logging.DEBUG)
    async with running_mac(FakeBlueBubbles()) as (service, sink, _):
        await sink.wait_for(connected)
        await service.messages(DM, before=None, limit=10)
    assert "password=" not in caplog.text


async def test_unexpected_reply_marks_send_failed_without_crashing() -> None:
    server = FakeBlueBubbles()

    def broken(request: httpx.Request) -> httpx.Response | None:
        if request.url.path.endswith("/message/text"):
            return httpx.Response(200, json={"status": 200, "data": {"unexpected": True}})
        return None

    server.override = broken
    async with running_mac(server) as (service, sink, _):
        await sink.wait_for(connected)
        await service.send_text(DM, "hello", reply_to=None, temp_id="bad")
        failed = await sink.wait_for(
            lambda u: isinstance(u, MessageUpdate) and u.message.status is Delivery.FAILED
        )
        assert isinstance(failed, MessageUpdate)
        assert failed.message.error == "Unexpected reply from the Mac."
        # The service is still alive and answering.
        assert (await service.chat(DM)).id == DM


async def test_mac_reporting_a_send_early_still_replaces_the_placeholder() -> None:
    server = FakeBlueBubbles()
    feed = FakeEvents()

    async def echo_first(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/message/text"):
            # The Mac announces the new message before answering the send call.
            feed.push(
                "new-message",
                {
                    "guid": "sent1",
                    "text": "Running late",
                    "isFromMe": True,
                    "dateCreated": BASE + 60 * 60_000,
                    "chats": [{"guid": DM}],
                },
            )
            await asyncio.sleep(0.2)
        return server.handle(request)

    async with running_mac(server, feed, transport=httpx.MockTransport(echo_first)) as (service, sink, _):
        await sink.wait_for(connected)
        await service.send_text(DM, "Running late", reply_to=None, temp_id="t9")
        await sink.wait_for(lambda u: isinstance(u, MessageUpdate) and u.message.id == "sent1")
    first = next(u for u in sink.updates if isinstance(u, MessageUpdate) and u.message.id == "sent1")
    assert isinstance(first, MessageUpdate)
    assert first.message.temp_id == "t9"


async def test_chats_that_appear_later_show_up_without_reconnecting() -> None:
    server = FakeBlueBubbles()
    async with running_mac(server, refresh_every=0.05) as (service, sink, _):
        await sink.wait_for(connected)
        old = "iMessage;-;+15550193"
        server.chats[old] = {"guid": old, "style": 45, "participants": [SAM]}
        server.messages[old] = [
            message("o1", old, text="From last year", at=-500_000, sender=SAM, dateRead=BASE)
        ]
        update = await sink.wait_for(lambda u: isinstance(u, ChatUpdate) and u.chat.id == old)
        assert isinstance(update, ChatUpdate)
        assert (update.chat.title, update.chat.unread) == ("Sam Okafor", 0)
        assert old in {c.id for c in await service.chats()}


def call_status(status: CallStatus) -> Callable[[UiUpdate], bool]:
    return lambda u: isinstance(u, CallUpdate) and u.call.status is status


async def test_direct_call_rings_from_the_mac_then_moves_to_this_pc() -> None:
    helper = FakeCallHelper()
    async with running_mac(FakeBlueBubbles(), calls=helper) as (service, sink, _feed):
        await sink.wait_for(connected)
        direct = (await service.status()).features.direct_calls
        call = await service.start_call(DM, video=False)
        helper.move_to(CallPhase.ANSWERED)
        await sink.wait_for(call_status(CallStatus.ANSWERED))
        helper.move_to(CallPhase.WAITING_FOR_PC, link=LINK)
        await sink.wait_for(call_status(CallStatus.READY))
        link = await service.join_call(call.id)
        helper.move_to(CallPhase.ON_PC, link=LINK)
        await sink.wait_for(call_status(CallStatus.CONNECTED))
    assert direct
    assert helper.dialed == [("+15550142", False)]
    assert call.caller is not None
    assert (call.status, call.outgoing, call.is_video, call.caller.name) == (
        CallStatus.OUTGOING,
        True,
        False,
        "Maya Chen",
    )
    assert link == LINK
    assert [u.call.status for u in sink.updates if isinstance(u, CallUpdate)] == [
        CallStatus.OUTGOING,
        CallStatus.ANSWERED,
        CallStatus.READY,
        CallStatus.CONNECTED,
    ]
    assert helper.closed


async def test_direct_call_that_fails_ends_with_the_reason() -> None:
    helper = FakeCallHelper()
    async with running_mac(FakeBlueBubbles(), calls=helper) as (service, sink, _feed):
        await sink.wait_for(connected)
        call = await service.start_call(DM, video=True)
        with pytest.raises(RequestError, match="isn't ready"):
            await service.join_call(call.id)
        helper.move_to(CallPhase.FAILED, reason="The PC didn't join in time, so the call was ended.")
        ended = await sink.wait_for(call_status(CallStatus.ENDED))
        with pytest.raises(NotFoundError):
            await service.join_call(call.id)
    assert isinstance(ended, CallUpdate)
    assert ended.call.detail == "The PC didn't join in time, so the call was ended."


async def test_ending_a_direct_call_hangs_up_on_the_mac() -> None:
    helper = FakeCallHelper()
    async with running_mac(FakeBlueBubbles(), calls=helper) as (service, sink, _feed):
        await sink.wait_for(connected)
        call = await service.start_call(DM, video=True)
        await service.decline_call(call.id)
        ended = await sink.wait_for(call_status(CallStatus.ENDED))
    assert helper.ended == ["call-1"]
    assert isinstance(ended, CallUpdate)
    assert (ended.call.id, ended.call.outgoing) == ("call-1", True)


async def test_direct_calls_need_the_helper_and_one_person() -> None:
    missing, busy, ready = (
        FakeCallHelper(available=False),
        FakeCallHelper(dial_error="The Mac is already on a call."),
        FakeCallHelper(),
    )
    async with running_mac(FakeBlueBubbles(), calls=missing) as (service, sink, _feed):
        await sink.wait_for(connected)
        assert not (await service.status()).features.direct_calls
        with pytest.raises(UnsupportedError, match="FaceTime helper"):
            await service.start_call(DM, video=True)
    async with running_mac(FakeBlueBubbles(), calls=busy) as (service, sink, _feed):
        await sink.wait_for(connected)
        with pytest.raises(RequestError, match="already on a call"):
            await service.start_call(DM, video=True)
    async with running_mac(FakeBlueBubbles(), calls=ready) as (service, sink, _feed):
        await sink.wait_for(connected)
        with pytest.raises(RequestError, match="Group FaceTime"):
            await service.start_call(GROUP, video=True)
    assert ready.dialed == []


@pytest.mark.parametrize(
    ("avatar", "photo"),
    [(None, None), ("", None), ("not base64!", None), (base64.b64encode(b"\xff\xd8").decode(), b"\xff\xd8")],
)
def test_contact_photo_decoding(avatar: str | None, photo: bytes | None) -> None:
    assert BBContact(avatar=avatar).photo == photo


async def test_contact_photos_reach_people_and_are_served() -> None:
    server = FakeBlueBubbles()
    jpeg, png = b"\xff\xd8\xff\xe0maya", b"\x89PNGsam"
    server.contacts[0]["avatar"] = base64.b64encode(jpeg).decode()
    server.contacts[1]["avatar"] = base64.b64encode(png).decode()
    async with running_mac(server) as (service, sink, _feed):
        await sink.wait_for(connected)
        group = await service.chat(GROUP)
        urls = [person.photo for person in group.participants]
        served = [await service.photo(url.rsplit("/", 1)[1]) for url in urls if url]
        with pytest.raises(NotFoundError):
            await service.photo("unknown")
    assert [url is not None and url.startswith("/api/photos/") for url in urls] == [True, True, False]
    assert served == [Photo("image/jpeg", jpeg), Photo("image/png", png)]


def test_large_contact_photos_are_shrunk() -> None:
    big = io.BytesIO()
    Image.new("RGB", (1200, 900), "teal").save(big, format="PNG")
    small = thumbnail(big.getvalue())
    with Image.open(io.BytesIO(small)) as image:
        assert (image.format, max(image.size)) == ("JPEG", 160)
    assert thumbnail(b"not an image") == b"not an image"
