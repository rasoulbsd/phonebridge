from datetime import timedelta

from phonebridge.domain import Contact, Direction, MessageStatus, NewMessage, conversation_key
from phonebridge.storage import SqliteRepository
from tests.conftest import NOW


def incoming(
    key: str, text: str, *, minutes_ago: int = 0, sender: str = "+15550142", read: bool = False
) -> NewMessage:
    return NewMessage(
        dedupe_key=key,
        conversation_id=conversation_key(sender),
        direction=Direction.IN,
        address=sender,
        name="Maya",
        text=text,
        at=NOW - timedelta(minutes=minutes_ago),
        read=read,
        status=MessageStatus.RECEIVED,
        phone_handle=f"h-{key}",
        session_id="s1",
    )


async def test_add_message_ignores_duplicates(repo: SqliteRepository) -> None:
    first = await repo.add_message(incoming("k1", "hello"))
    assert first is not None
    assert first.text == "hello"
    assert await repo.add_message(incoming("k1", "hello")) is None
    assert await repo.known_keys(["k1", "k2"]) == {"k1"}


async def test_conversations_sorted_with_unread_counts_and_contact_names(repo: SqliteRepository) -> None:
    await repo.add_message(incoming("a1", "old", minutes_ago=30, sender="+15550178"))
    await repo.add_message(incoming("b1", "first", minutes_ago=20))
    await repo.add_message(incoming("b2", "second", minutes_ago=10))
    await repo.replace_contacts([Contact(name="Maya Chen", addresses=["+1 555 0142"])])

    conversations = await repo.list_conversations()

    assert [(c.id, c.name, c.last_text, c.unread) for c in conversations] == [
        ("p:15550142", "Maya Chen", "second", 2),
        ("p:15550178", "Maya", "old", 1),
    ]
    assert [c.id for c in await repo.list_conversations("0178")] == ["p:15550178"]


async def test_list_messages_pages_backwards(repo: SqliteRepository) -> None:
    for i in range(5):
        await repo.add_message(incoming(f"k{i}", f"m{i}", minutes_ago=10 - i))
    page, has_more = await repo.list_messages("p:15550142", before_id=None, limit=2)
    assert ([m.text for m in page], has_more) == (["m3", "m4"], True)
    older, has_more = await repo.list_messages("p:15550142", before_id=page[0].id, limit=10)
    assert ([m.text for m in older], has_more) == (["m0", "m1", "m2"], False)


async def test_mark_conversation_read_returns_changed_messages(repo: SqliteRepository) -> None:
    await repo.add_message(incoming("k1", "one"))
    await repo.add_message(incoming("k2", "two", read=True))
    changed = await repo.mark_conversation_read("p:15550142")
    assert [(m.text, m.phone_handle) for m in changed] == [("one", "h-k1")]
    assert await repo.mark_conversation_read("p:15550142") == []


async def test_mark_read_by_keys_reports_conversations(repo: SqliteRepository) -> None:
    await repo.add_message(incoming("k1", "one"))
    await repo.add_message(incoming("k2", "two", sender="+15550178", read=True))
    assert await repo.mark_read_by_keys(["k1", "k2", "missing"]) == ["p:15550142"]


async def test_update_status_keeps_existing_handle(repo: SqliteRepository) -> None:
    saved = await repo.add_message(incoming("k1", "one"))
    assert saved is not None
    updated = await repo.update_message_status(saved.id, MessageStatus.FAILED, error="nope")
    assert (updated.status, updated.error, updated.phone_handle) == (MessageStatus.FAILED, "nope", "h-k1")


async def test_search_matches_prefixes_and_unicode(repo: SqliteRepository) -> None:
    await repo.add_message(incoming("k1", "Lunch tomorrow?"))
    await repo.add_message(incoming("k2", "سلام، فردا میای؟", sender="+15550178"))
    assert [h.message.text for h in await repo.search("lun", 10)] == ["Lunch tomorrow?"]
    assert [h.conversation.id for h in await repo.search("فردا", 10)] == ["p:15550178"]
    assert await repo.search('"; DROP TABLE', 10) == []


async def test_contacts_search_and_count(repo: SqliteRepository) -> None:
    await repo.replace_contacts(
        [
            Contact(name="Maya Chen", addresses=["+15550142", "maya@example.com"]),
            Contact(name="Jordan Rivera", addresses=["+15550178"]),
        ]
    )
    assert await repo.contacts_count() == 2
    assert await repo.search_contacts("example", 10) == [
        Contact(name="Maya Chen", addresses=["+15550142", "maya@example.com"])
    ]
    assert [c.name for c in await repo.search_contacts("", 10)] == ["Jordan Rivera", "Maya Chen"]


async def test_meta_round_trip(repo: SqliteRepository) -> None:
    assert await repo.get_meta("x") is None
    await repo.set_meta("x", "1")
    await repo.set_meta("x", "2")
    assert await repo.get_meta("x") == "2"
