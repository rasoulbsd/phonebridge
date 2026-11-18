from datetime import UTC, datetime, timedelta, timezone

import pytest

from phonebridge.protocol import (
    build_bmessage,
    parse_bmessage,
    parse_event_report,
    parse_map_datetime,
    parse_message_listing,
    parse_vcards,
)
from phonebridge.protocol.vcard import VCard

TEHRAN = timezone(timedelta(hours=3, minutes=30))

BMESSAGE = (
    "BEGIN:BMSG\r\nVERSION:1.0\r\nSTATUS:UNREAD\r\nTYPE:SMS_GSM\r\nFOLDER:TELECOM/MSG/INBOX\r\n"
    "BEGIN:VCARD\r\nVERSION:2.1\r\nN:Chen;Maya\r\nTEL:+15550142\r\nEND:VCARD\r\n"
    "BEGIN:BENV\r\nBEGIN:VCARD\r\nVERSION:2.1\r\nN:\r\nTEL:+15550199\r\nEND:VCARD\r\n"
    "BEGIN:BBODY\r\nCHARSET:UTF-8\r\nLENGTH:44\r\nBEGIN:MSG\r\nFirst line\r\nسلام دوست\r\nEND:MSG\r\n"
    "END:BBODY\r\nEND:BENV\r\nEND:BMSG\r\n"
)


def test_parse_bmessage() -> None:
    message = parse_bmessage(BMESSAGE)
    assert message.status == "UNREAD"
    assert message.msg_type == "SMS_GSM"
    assert message.originator == VCard(name="Maya Chen", phones=("+15550142",), emails=())
    assert message.recipients == (VCard(name="", phones=("+15550199",), emails=()),)
    assert message.text == "First line\nسلام دوست"


@pytest.mark.parametrize(
    ("recipient", "field"), [("+15550142", "TEL:+15550142"), ("maya@example.com", "EMAIL:maya@example.com")]
)
def test_build_bmessage_round_trips(recipient: str, field: str) -> None:
    raw = build_bmessage(recipient, "Hi there\nسلام").decode("utf-8")
    assert field in raw
    length = int(raw.split("LENGTH:")[1].split("\r\n")[0])
    body = raw[raw.index("BEGIN:MSG") : raw.index("END:MSG") + len("END:MSG\r\n")]
    assert length == len(body.encode("utf-8"))
    assert parse_bmessage(raw).text == "Hi there\nسلام"


def test_parse_vcards_handles_quoted_printable_folding_and_groups() -> None:
    data = (
        "BEGIN:VCARD\r\nVERSION:2.1\r\nN;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:=D8=B3=D8=A7=D8=B1=\r\n=D8=A7;\r\n"
        "TEL;CELL:+98 912 123 4567\r\nEND:VCARD\r\n"
        "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Jordan\r\n  Rivera\r\nitem1.EMAIL;type=INTERNET:jordan@example.com\r\n"
        "TEL;TYPE=CELL:+15550178\r\nEND:VCARD\r\n"
        "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:No Number\r\nEND:VCARD\r\n"
    )
    assert parse_vcards(data) == [
        VCard(name="سارا", phones=("+98 912 123 4567",), emails=()),
        VCard(name="Jordan Rivera", phones=("+15550178",), emails=("jordan@example.com",)),
        VCard(name="No Number", phones=(), emails=()),
    ]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("20261030T091500", datetime(2026, 10, 30, 9, 15, tzinfo=TEHRAN)),
        ("20261030T091500Z", datetime(2026, 10, 30, 9, 15, tzinfo=UTC)),
        ("20261030T091500-0500", datetime(2026, 10, 30, 9, 15, tzinfo=timezone(timedelta(hours=-5)))),
    ],
)
def test_parse_map_datetime(raw: str, expected: datetime) -> None:
    parsed = parse_map_datetime(raw, TEHRAN)
    assert (parsed, parsed.utcoffset()) == (expected, expected.utcoffset())


def test_parse_map_datetime_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="unrecognised"):
        parse_map_datetime("yesterday", UTC)


def test_parse_message_listing_validates_and_skips_bad_entries() -> None:
    xml = (
        b'<MAP-msg-listing version="1.0">'
        b'<msg handle="20000100001" subject="Hi" datetime="20261030T091500" sender_name="Maya" '
        b'sender_addressing="+15550142" type="SMS_GSM" size="2" attachment_size="0" read="no" sent="no"/>'
        b'<msg handle="20000100002" subject="broken" datetime="not-a-date"/>'
        b"</MAP-msg-listing>"
    )
    [item] = parse_message_listing(xml, TEHRAN)
    assert item.handle == "20000100001"
    assert item.sent_at == datetime(2026, 10, 30, 9, 15, tzinfo=TEHRAN)
    assert (item.read, item.size, item.has_full_text) == (False, 2, True)


def test_has_full_text_detects_truncated_subjects() -> None:
    [item] = parse_message_listing(
        b'<MAP-msg-listing><msg handle="1" subject="Short preview" datetime="20261030T091500" size="900"/>'
        b"</MAP-msg-listing>",
        UTC,
    )
    assert not item.has_full_text


def test_empty_listing() -> None:
    assert parse_message_listing(b"  ", UTC) == []


def test_parse_event_report() -> None:
    [event] = parse_event_report(
        b'<MAP-event-report version="1.0"><event type="NewMessage" handle="20000100003" '
        b'folder="TELECOM/MSG/INBOX" msg_type="SMS_GSM"/></MAP-event-report>'
    )
    assert (event.kind, event.handle, event.folder) == ("NewMessage", "20000100003", "TELECOM/MSG/INBOX")
