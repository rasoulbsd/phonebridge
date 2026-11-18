import asyncio

import pytest

from phonebridge.domain import (
    ConnectionState,
    Contact,
    ConversationEvent,
    MessageEvent,
    MessageStatus,
    NotificationMode,
    StatusEvent,
    UiEvent,
)
from phonebridge.services import (
    NOT_CONNECTED,
    InvalidMessageError,
    PhoneBusyError,
    PhoneDisconnectedError,
    PhoneError,
    PhoneNotPairedError,
    PhoneRequestError,
)
from phonebridge.storage import SqliteRepository
from tests.conftest import (
    FakeConnector,
    FakePhoneLink,
    FakeSession,
    RecordingSink,
    listed,
    make_bridge,
    running,
)


def is_connected(event: UiEvent) -> bool:
    return isinstance(event, StatusEvent) and event.status.state is ConnectionState.CONNECTED


async def test_connect_saves_inbox_and_publishes_messages(
    repo: SqliteRepository, sink: RecordingSink
) -> None:
    session = FakeSession([listed("h1", text="older", minutes_ago=5), listed("h2", text="newer")])
    bridge = make_bridge(FakeConnector(session), repo, sink)

    async with running(bridge):
        await sink.wait_for(lambda e: isinstance(e, MessageEvent) and e.message.text == "newer")

    [conversation] = await bridge.conversations()
    assert (conversation.name, conversation.last_text, conversation.unread) == ("Maya Chen", "newer", 2)
    status = next(e.status for e in sink.events if is_connected(e) and isinstance(e, StatusEvent))
    assert (status.phone_name, status.notifications) == ("Test iPhone", NotificationMode.PUSH)


async def test_refresh_after_phone_event_only_adds_new_messages(
    repo: SqliteRepository, sink: RecordingSink
) -> None:
    session = FakeSession([listed("h1", text="first")])
    bridge = make_bridge(FakeConnector(session), repo, sink)

    async with running(bridge):
        await sink.wait_for(lambda e: isinstance(e, MessageEvent))
        mark = sink.mark()
        session.inbox.append(listed("h2", text="second"))
        session.notify()
        await sink.wait_for(lambda e: isinstance(e, MessageEvent), since=mark)

    texts = [e.message.text for e in sink.events if isinstance(e, MessageEvent)]
    assert texts == ["first", "second"]


async def test_truncated_messages_are_fetched_and_left_unread(
    repo: SqliteRepository, sink: RecordingSink
) -> None:
    long_text = "x" * 400
    session = FakeSession([listed("h1", text="x" * 255, size=400)], texts={"h1": long_text})
    bridge = make_bridge(FakeConnector(session), repo, sink)

    async with running(bridge):
        event = await sink.wait_for(lambda e: isinstance(e, MessageEvent))

    assert isinstance(event, MessageEvent)
    assert event.message.text == long_text
    assert session.fetches == ["h1"]
    assert session.read_calls == [("h1", False)]


async def test_read_on_phone_clears_unread_here(repo: SqliteRepository, sink: RecordingSink) -> None:
    session = FakeSession([listed("h1")])
    bridge = make_bridge(FakeConnector(session), repo, sink)

    async with running(bridge):
        await sink.wait_for(lambda e: isinstance(e, MessageEvent))
        mark = sink.mark()
        session.inbox = [listed("h1", read=True)]
        session.notify()
        event = await sink.wait_for(lambda e: isinstance(e, ConversationEvent), since=mark)

    assert isinstance(event, ConversationEvent)
    assert event.conversation.unread == 0


async def test_mark_read_updates_phone_for_current_session(
    repo: SqliteRepository, sink: RecordingSink
) -> None:
    session = FakeSession([listed("h1"), listed("h2", minutes_ago=1, read=True)])
    bridge = make_bridge(FakeConnector(session), repo, sink)

    async with running(bridge):
        await sink.wait_for(is_connected)
        await sink.wait_for(lambda e: isinstance(e, MessageEvent) and not e.message.read)
        await bridge.mark_read("p:15550142")

    assert session.read_calls == [("h1", True)]
    [conversation] = await bridge.conversations()
    assert conversation.unread == 0


async def test_send_reports_sending_then_sent(repo: SqliteRepository, sink: RecordingSink) -> None:
    session = FakeSession()
    bridge = make_bridge(FakeConnector(session), repo, sink)

    async with running(bridge):
        await sink.wait_for(is_connected)
        message = await bridge.send("+1 555 0142", "On my way")
        sent = await sink.wait_for(
            lambda e: isinstance(e, MessageEvent) and e.message.status is MessageStatus.SENT
        )

    assert message.status is MessageStatus.SENDING
    assert isinstance(sent, MessageEvent)
    assert (sent.message.id, sent.message.conversation_id) == (message.id, "p:15550142")
    assert session.sent == [("+15550142", "On my way")]


async def test_send_failure_is_saved_with_reason(repo: SqliteRepository, sink: RecordingSink) -> None:
    session = FakeSession()
    session.send_error = PhoneRequestError("PUT refused by the phone (OBEX 0xC0)")
    bridge = make_bridge(FakeConnector(session), repo, sink)

    async with running(bridge):
        await sink.wait_for(is_connected)
        await bridge.send("+15550142", "hello")
        failed = await sink.wait_for(
            lambda e: isinstance(e, MessageEvent) and e.message.status is MessageStatus.FAILED
        )

    assert isinstance(failed, MessageEvent)
    assert failed.message.error == "PUT refused by the phone (OBEX 0xC0)"


async def test_send_without_phone_fails_immediately(repo: SqliteRepository, sink: RecordingSink) -> None:
    bridge = make_bridge(FakeConnector(), repo, sink)
    message = await bridge.send("+15550142", "hello")
    assert (message.status, message.error) == (MessageStatus.FAILED, NOT_CONNECTED)


async def test_retry_requeues_failed_message(repo: SqliteRepository, sink: RecordingSink) -> None:
    session = FakeSession()
    bridge = make_bridge(FakeConnector(session), repo, sink)
    failed = await bridge.send("+15550142", "hello")

    async with running(bridge):
        await sink.wait_for(is_connected)
        retried = await bridge.retry(failed.id)
        await sink.wait_for(lambda e: isinstance(e, MessageEvent) and e.message.status is MessageStatus.SENT)

    assert retried.status is MessageStatus.SENDING
    assert session.sent == [("+15550142", "hello")]


@pytest.mark.parametrize(("address", "text"), [("", "hi"), ("+15550142", "   "), ("+15550142", "x" * 5000)])
async def test_send_rejects_invalid_input(
    repo: SqliteRepository, sink: RecordingSink, address: str, text: str
) -> None:
    bridge = make_bridge(FakeConnector(), repo, sink)
    with pytest.raises(InvalidMessageError):
        await bridge.send(address, text)


@pytest.mark.parametrize(
    ("error", "state"),
    [
        (PhoneBusyError("Phone Link is using it"), ConnectionState.BUSY),
        (PhoneNotPairedError("pair it"), ConnectionState.NO_PHONE),
    ],
)
async def test_connection_problems_become_status(
    repo: SqliteRepository, sink: RecordingSink, error: PhoneError, state: ConnectionState
) -> None:
    bridge = make_bridge(FakeConnector(error), repo, sink)
    async with running(bridge):
        event = await sink.wait_for(lambda e: isinstance(e, StatusEvent) and e.status.state is state)
    assert isinstance(event, StatusEvent)
    assert event.status.detail == str(error)


async def test_close_phone_link_triggers_reconnect(repo: SqliteRepository, sink: RecordingSink) -> None:
    session = FakeSession()
    phone_link = FakePhoneLink()
    connector = FakeConnector(PhoneBusyError("busy"), session)
    bridge = make_bridge(connector, repo, sink, phone_link=phone_link)

    async with running(bridge):
        await sink.wait_for(lambda e: isinstance(e, StatusEvent) and e.status.state is ConnectionState.BUSY)
        assert await bridge.close_phone_link()
        await sink.wait_for(is_connected)

    assert (phone_link.calls, connector.attempts) == (1, 2)


async def test_contacts_synced_on_connect(repo: SqliteRepository, sink: RecordingSink) -> None:
    session = FakeSession([listed("h1")], contacts=[Contact(name="Maya Chen", addresses=["+15550142"])])
    bridge = make_bridge(FakeConnector(session), repo, sink)

    async with running(bridge):
        # Contacts sync before the first inbox refresh, so a saved message means they're done.
        await sink.wait_for(lambda e: isinstance(e, MessageEvent))

    assert [c.name for c in await bridge.contacts("maya")] == ["Maya Chen"]
    assert (await bridge.status()).contacts_count == 1


async def test_slow_contact_sync_does_not_delay_messages(repo: SqliteRepository, sink: RecordingSink) -> None:
    session = FakeSession([listed("h1")], contacts=[Contact(name="Maya Chen", addresses=["+15550142"])])
    session.contacts_gate = asyncio.Event()
    bridge = make_bridge(FakeConnector(session), repo, sink)

    async with running(bridge):
        await sink.wait_for(lambda e: isinstance(e, MessageEvent))
        assert (await bridge.status()).contacts_count == 0
        session.contacts_gate.set()
        await sink.wait_for(lambda e: isinstance(e, StatusEvent) and e.status.contacts_count == 1)


async def test_disconnect_marks_status_and_closes_session(
    repo: SqliteRepository, sink: RecordingSink
) -> None:
    session = FakeSession()
    bridge = make_bridge(FakeConnector(session), repo, sink)

    async with running(bridge):
        await sink.wait_for(is_connected)
        mark = sink.mark()
        session.list_error = PhoneDisconnectedError("gone")
        session.notify()
        await sink.wait_for(
            lambda e: isinstance(e, StatusEvent) and e.status.state is ConnectionState.DISCONNECTED,
            since=mark,
        )

    assert session.closed
