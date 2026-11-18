import struct

import pytest

from phonebridge.protocol.obex import (
    Hdr,
    ObexClient,
    ObexError,
    ObexServer,
    Op,
    PutRequest,
    Rsp,
    app_params_header,
    bytes_header,
    encode_packet,
    parse_headers,
    parse_tlvs,
    type_header,
    u32_header,
    unicode_header,
)
from tests.conftest import FakeStream

TARGET = bytes(range(16))


def connect_response(max_packet: int = 0x0400, conn_id: int = 7) -> bytes:
    return encode_packet(
        Rsp.SUCCESS,
        bytes([0x10, 0x00]) + struct.pack(">H", max_packet) + u32_header(Hdr.CONNECTION_ID, conn_id),
    )


def split_packets(data: bytes) -> list[tuple[int, bytes]]:
    packets = []
    while data:
        length = struct.unpack(">H", data[1:3])[0]
        packets.append((data[0], data[3:length]))
        data = data[length:]
    return packets


def test_header_round_trip() -> None:
    raw = (
        unicode_header(Hdr.NAME, "inbox")
        + type_header("x-bt/message")
        + u32_header(Hdr.CONNECTION_ID, 9)
        + app_params_header({0x01: b"\x00\x10"})
    )
    headers = parse_headers(raw)
    assert [(h.id, h.value) for h in headers] == [
        (Hdr.NAME, "inbox\0".encode("utf-16-be")),
        (Hdr.TYPE, b"x-bt/message\0"),
        (Hdr.CONNECTION_ID, 9),
        (Hdr.APP_PARAMS, b"\x01\x02\x00\x10"),
    ]
    assert parse_tlvs(b"\x01\x02\x00\x10\x13\x01\xff") == {0x01: b"\x00\x10", 0x13: b"\xff"}


async def test_connect_negotiates_packet_size_and_connection_id() -> None:
    stream = FakeStream(connect_response(max_packet=0x0400, conn_id=7))
    client = ObexClient(stream)
    await client.connect(TARGET)
    assert client.max_packet == 0x0400
    [(code, payload)] = split_packets(bytes(stream.written))
    assert code == Op.CONNECT
    assert parse_headers(payload[4:])[0].value == TARGET


async def test_get_collects_body_across_continue_responses() -> None:
    stream = FakeStream(
        connect_response()
        + encode_packet(Rsp.CONTINUE, bytes_header(Hdr.BODY, b"<a>"))
        + encode_packet(
            Rsp.SUCCESS, bytes_header(Hdr.END_OF_BODY, b"</a>") + app_params_header({0x12: b"\x00\x02"})
        )
    )
    client = ObexClient(stream)
    await client.connect(TARGET)
    result = await client.get(type_header("x-bt/thing"))
    assert (result.body, result.app_params) == (b"<a></a>", {0x12: b"\x00\x02"})
    requests = split_packets(bytes(stream.written))[1:]
    assert [code for code, _ in requests] == [Op.GET_FINAL, Op.GET_FINAL]
    # The connection id must lead every request.
    assert all(parse_headers(payload)[0].value == 7 for _, payload in requests)


async def test_put_splits_large_bodies_by_negotiated_packet_size() -> None:
    body = bytes(range(256)) * 4
    stream = FakeStream(
        connect_response(max_packet=300)
        + encode_packet(Rsp.CONTINUE, b"") * 3
        + encode_packet(Rsp.SUCCESS, unicode_header(Hdr.NAME, "123"))
    )
    client = ObexClient(stream)
    await client.connect(TARGET)
    response = await client.put(type_header("x-bt/message"), body)
    requests = split_packets(bytes(stream.written))[1:]
    assert all(len(payload) + 3 <= 300 for _, payload in requests)
    assert [code for code, _ in requests][-1] == Op.PUT_FINAL
    sent = b"".join(
        h.value
        for _, p in requests
        for h in parse_headers(p)
        if h.id in (Hdr.BODY, Hdr.END_OF_BODY) and isinstance(h.value, bytes)
    )
    assert sent == body
    assert response.first_bytes(Hdr.NAME) == "123\0".encode("utf-16-be")


async def test_refused_request_raises_with_code() -> None:
    stream = FakeStream(connect_response() + encode_packet(0xC4, b""))
    client = ObexClient(stream)
    await client.connect(TARGET)
    with pytest.raises(ObexError) as caught:
        await client.setpath("telecom")
    assert caught.value.code == 0xC4


async def test_server_accepts_connect_and_delivers_put() -> None:
    connect = bytes([0x10, 0x00]) + struct.pack(">H", 0x1000) + bytes_header(Hdr.TARGET, TARGET)
    put_first = type_header("x-bt/MAP-event-report") + bytes_header(Hdr.BODY, b"<x")
    put_final = bytes_header(Hdr.END_OF_BODY, b"/>")
    stream = FakeStream(
        encode_packet(Op.CONNECT, connect)
        + encode_packet(Op.PUT, put_first)
        + encode_packet(Op.PUT_FINAL, put_final)
        + encode_packet(Op.DISCONNECT, b"")
    )
    received: list[PutRequest] = []

    async def on_put(request: PutRequest) -> None:
        received.append(request)

    await ObexServer(stream, target=TARGET).serve(on_put)

    assert received == [PutRequest(type="x-bt/MAP-event-report", name=None, app_params={}, body=b"<x/>")]
    assert [code for code, _ in split_packets(bytes(stream.written))] == [
        Rsp.SUCCESS,
        Rsp.CONTINUE,
        Rsp.SUCCESS,
        Rsp.SUCCESS,
    ]


async def test_server_rejects_wrong_target() -> None:
    connect = bytes([0x10, 0x00]) + struct.pack(">H", 0x1000) + bytes_header(Hdr.TARGET, b"\x00" * 16)

    async def on_put(_request: PutRequest) -> None:
        raise AssertionError("no PUT expected")

    stream = FakeStream(encode_packet(Op.CONNECT, connect))
    await ObexServer(stream, target=TARGET).serve(on_put)
    assert split_packets(bytes(stream.written))[0][0] == Rsp.NOT_ACCEPTABLE
