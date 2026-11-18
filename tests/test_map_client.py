import struct
from datetime import UTC

from phonebridge.protocol import MapClient, ObexClient
from phonebridge.protocol.map import MapParam
from phonebridge.protocol.obex import (
    Hdr,
    Op,
    Rsp,
    bytes_header,
    decode_type,
    decode_unicode,
    encode_packet,
    parse_headers,
    parse_tlvs,
    u32_header,
    unicode_header,
)
from tests.conftest import FakeStream

OK = encode_packet(Rsp.SUCCESS, b"")


def opened_client(*responses: bytes) -> tuple[MapClient, FakeStream]:
    connect = encode_packet(
        Rsp.SUCCESS, bytes([0x10, 0x00]) + struct.pack(">H", 0x1000) + u32_header(Hdr.CONNECTION_ID, 1)
    )
    stream = FakeStream(connect + OK * 3 + b"".join(responses))
    return MapClient(ObexClient(stream), UTC), stream


def last_request(stream: FakeStream) -> tuple[int, dict[int, bytes | int]]:
    data = bytes(stream.written)
    packets = []
    while data:
        length = struct.unpack(">H", data[1:3])[0]
        packets.append((data[0], data[3:length]))
        data = data[length:]
    code, payload = packets[-1]
    return code, {h.id: h.value for h in parse_headers(payload)}


async def test_open_enters_telecom_msg() -> None:
    client, stream = opened_client()
    await client.open()
    assert stream.incoming == b""


async def test_list_messages_requests_inbox_with_long_subjects() -> None:
    listing = (
        b'<MAP-msg-listing version="1.0"><msg handle="1" subject="hey" datetime="20261030T120000Z" '
        b'sender_addressing="+15550142" size="3" read="yes"/></MAP-msg-listing>'
    )
    client, stream = opened_client(encode_packet(Rsp.SUCCESS, bytes_header(Hdr.END_OF_BODY, listing)))
    await client.open()
    [item] = await client.list_messages()

    code, headers = last_request(stream)
    name, mime, params = headers[Hdr.NAME], headers[Hdr.TYPE], headers[Hdr.APP_PARAMS]
    assert isinstance(name, bytes)
    assert isinstance(mime, bytes)
    assert isinstance(params, bytes)
    assert (code, decode_unicode(name), decode_type(mime)) == (Op.GET_FINAL, "inbox", "x-bt/MAP-msg-listing")
    assert parse_tlvs(params)[MapParam.SUBJECT_LENGTH] == b"\xff"
    assert (item.subject, item.read) == ("hey", True)


async def test_push_message_returns_new_handle() -> None:
    client, stream = opened_client(encode_packet(Rsp.SUCCESS, unicode_header(Hdr.NAME, "20000100009")))
    await client.open()
    assert await client.push_message("+15550142", "On my way") == "20000100009"
    code, headers = last_request(stream)
    body = headers[Hdr.END_OF_BODY]
    assert code == Op.PUT_FINAL
    assert isinstance(body, bytes)
    assert b"TEL:+15550142" in body


async def test_set_read_sends_status_parameters() -> None:
    client, stream = opened_client(OK)
    await client.open()
    await client.set_read("20000100001", read=True)
    _, headers = last_request(stream)
    params = headers[Hdr.APP_PARAMS]
    assert isinstance(params, bytes)
    assert parse_tlvs(params) == {MapParam.STATUS_INDICATOR: b"\x00", MapParam.STATUS_VALUE: b"\x01"}
    assert headers[Hdr.END_OF_BODY] == b"0"
