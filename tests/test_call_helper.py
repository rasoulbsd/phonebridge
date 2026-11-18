from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest

from phonebridge.mac.call_helper import (
    CallHelperClient,
    CallHelperError,
    CallHelperUnavailableError,
    CallPhase,
    HelperCall,
    helper_url,
)

if TYPE_CHECKING:
    from collections.abc import Callable

type Handler = Callable[[httpx.Request], httpx.Response]


def client(handler: Handler) -> CallHelperClient:
    return CallHelperClient("http://mac.test:1235", "pw", transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("server", "helper"),
    [
        ("http://192.168.1.20:1234", "http://192.168.1.20:1235"),
        ("https://mac.local", "http://mac.local:1235"),
        ("http://[fe80::1]:1234", "http://[fe80::1]:1235"),
    ],
)
def test_helper_lives_next_to_bluebubbles(server: str, helper: str) -> None:
    assert helper_url(server) == helper


async def test_dial_sends_the_password_and_reads_the_call() -> None:
    seen: list[tuple[str, str, str | None, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        token = request.headers.get("X-Phonebridge-Token")
        seen.append((request.method, request.url.path, token, json.loads(request.content)))
        return httpx.Response(201, json={"id": "c1", "phase": "calling"})

    helper = client(handler)
    call = await helper.dial("+15550142", video=False)
    await helper.close()
    assert call == HelperCall(id="c1", phase=CallPhase.CALLING)
    assert seen == [("POST", "/calls", "pw", {"handle": "+15550142", "video": False})]


async def test_call_ids_are_escaped_in_paths() -> None:
    paths: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.raw_path)
        return httpx.Response(200, json={"id": "a/b", "phase": "on_pc", "link": "https://x"})

    helper = client(handler)
    await helper.call("a/b")
    await helper.end("a/b")
    await helper.close()
    assert paths == [b"/calls/a%2Fb", b"/calls/a%2Fb/end"]


def _refuse(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=request)


@pytest.mark.parametrize(
    ("handler", "expected"),
    [
        (lambda _r: httpx.Response(200, json={"ok": True}), True),
        (lambda _r: httpx.Response(401, json={"error": "Wrong password."}), False),
        (_refuse, False),
    ],
)
async def test_available_only_when_the_helper_answers(handler: Handler, *, expected: bool) -> None:
    helper = client(handler)
    assert await helper.available() is expected
    await helper.close()


@pytest.mark.parametrize(
    ("handler", "error", "message"),
    [
        (
            lambda _r: httpx.Response(409, json={"error": "The Mac is already on a call."}),
            CallHelperError,
            "The Mac is already on a call.",
        ),
        (lambda _r: httpx.Response(401, json={}), CallHelperUnavailableError, "rejected the password"),
        (lambda _r: httpx.Response(200, json={"phase": "calling"}), CallHelperError, "Unexpected reply"),
        (lambda _r: httpx.Response(502, text="<html>"), CallHelperError, "Unexpected reply"),
        (_refuse, CallHelperUnavailableError, "isn't reachable"),
    ],
)
async def test_failures_become_helper_errors(handler: Handler, error: type[Exception], message: str) -> None:
    helper = client(handler)
    with pytest.raises(error, match=message):
        await helper.dial("+15550142", video=True)
    await helper.close()
