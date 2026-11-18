from __future__ import annotations

import asyncio
import base64
import contextlib
from typing import TYPE_CHECKING
from urllib.parse import quote

import httpx
from pydantic import SecretStr

from phonebridge.config import ConfigStore, MacConnection, UserConfig
from phonebridge.domain.ui import CallStatus, CallUpdate, State, StatusUpdate
from phonebridge.mac.call_helper import CallPhase
from phonebridge.mac.client import BlueBubblesClient
from phonebridge.runtime import FanoutSink
from phonebridge.services.mac_service import MacService
from phonebridge.services.manager import ServiceManager
from phonebridge.services.phone_service import PhoneService
from phonebridge.web import WebSocketHub, create_app
from tests.conftest import FakeConnector, UpdateRecorder
from tests.fake_bluebubbles import DM, GROUP, FakeBlueBubbles, FakeEvents
from tests.fake_call_helper import LINK, FakeCallHelper

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from phonebridge.services.messaging import MessagingService
    from phonebridge.storage import SqliteRepository

HOST = "127.0.0.1:8765"
CSRF = {"X-Requested-With": "phonebridge"}


class FakeDesktop:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def show_window(self) -> None:
        self.calls.append("show")

    def quit_app(self) -> None:
        self.calls.append("quit")


class Harness:
    def __init__(self, client: httpx.AsyncClient, server: FakeBlueBubbles, sink: UpdateRecorder) -> None:
        self.client = client
        self.server = server
        self.sink = sink
        self.opened: list[str] = []


@contextlib.asynccontextmanager
async def harness(
    tmp_path: Path,
    repo: SqliteRepository,
    *,
    mac: bool = False,
    token: str | None = None,
    desktop: FakeDesktop | None = None,
    server: FakeBlueBubbles | None = None,
    calls: FakeCallHelper | None = None,
) -> AsyncGenerator[Harness]:
    server = server or FakeBlueBubbles()
    recorder = UpdateRecorder()
    hub = WebSocketHub()
    sink = FanoutSink([hub, recorder])
    store = ConfigStore(tmp_path / "config.json")
    if mac:
        store.save(UserConfig(mac=MacConnection(url="http://mac.test:1234", password=SecretStr("pw"))))

    def factory(config: UserConfig) -> MessagingService:
        if config.mac is None:
            return PhoneService(FakeConnector(), repo, sink)
        client = BlueBubblesClient(
            config.mac.url, config.mac.password.get_secret_value(), transport=server.transport()
        )
        return MacService(client, FakeEvents, sink, calls=calls, call_poll=0.01)

    manager = ServiceManager(store, factory, sink, probe_transport=server.transport())
    await manager.start()
    task = asyncio.create_task(manager.run())
    opened: list[str] = []

    async def opener(link: str) -> bool:
        opened.append(link)
        return True

    app = create_app(manager, hub, token=token, allowed_hosts={HOST}, desktop=desktop, open_link=opener)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    try:
        async with httpx.AsyncClient(transport=transport, base_url=f"http://{HOST}") as client:
            h = Harness(client, server, recorder)
            h.opened = opened
            if mac:
                await recorder.wait_for(
                    lambda u: isinstance(u, StatusUpdate) and u.status.state is State.CONNECTED
                )
            yield h
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await manager.current.close()


def path_id(value: str) -> str:
    return quote(value, safe="")


async def test_phone_mode_status_and_limited_features(tmp_path: Path, repo: SqliteRepository) -> None:
    async with harness(tmp_path, repo) as h:
        status = (await h.client.get("/api/status")).json()
        assert (status["mode"], status["features"]["reactions"], status["features"]["search"]) == (
            "phone",
            False,
            True,
        )
        assert (await h.client.get("/api/chats")).json() == []
        settings = (await h.client.get("/api/settings")).json()
        assert settings == {"mode": "phone", "mac": None}


async def test_phone_mode_new_chat_and_unsupported_actions(tmp_path: Path, repo: SqliteRepository) -> None:
    async with harness(tmp_path, repo) as h:
        created = await h.client.post(
            "/api/chats", json={"addresses": ["+1 555 0142"], "text": "Hi"}, headers=CSRF
        )
        assert created.status_code == 201
        page = created.json()
        assert page["chat"]["id"] == "p:15550142"
        assert [(m["text"], m["status"]) for m in page["messages"]] == [("Hi", "failed")]
        group = await h.client.post(
            "/api/chats", json={"addresses": ["+15550142", "+15550193"], "text": "x"}, headers=CSRF
        )
        assert group.status_code == 409
        assert "Mac" in group.json()["detail"]
        react = await h.client.post(
            "/api/messages/1/reactions", json={"chat_id": "p:15550142", "type": "love"}, headers=CSRF
        )
        assert react.status_code == 409


async def test_security_checks(tmp_path: Path, repo: SqliteRepository) -> None:
    async with harness(tmp_path, repo) as h:
        no_csrf = await h.client.post("/api/chats", json={"addresses": ["+15550142"], "text": "Hi"})
        cross_site = await h.client.post(
            "/api/chats/x/read", headers={**CSRF, "Origin": "https://evil.example"}
        )
        wrong_host = await h.client.get("/api/status", headers={"Host": "attacker.example:8765"})
        assert (no_csrf.status_code, cross_site.status_code, wrong_host.status_code) == (403, 403, 400)


async def test_token_required_when_configured(tmp_path: Path, repo: SqliteRepository) -> None:
    async with harness(tmp_path, repo, token="s3cret") as h:
        assert (await h.client.get("/api/status")).status_code == 401
        assert (await h.client.get("/api/status", params={"token": "s3cret"})).status_code == 200
        # The token from the link is kept in a cookie for later requests.
        assert (await h.client.get("/api/status")).status_code == 200
        assert (await h.client.get("/api/status", headers={"Host": "my-pc.tailnet:8765"})).status_code == 200


async def test_desktop_endpoints(tmp_path: Path, repo: SqliteRepository) -> None:
    desktop = FakeDesktop()
    async with harness(tmp_path, repo, desktop=desktop) as h:
        assert (await h.client.post("/api/desktop/show", headers=CSRF)).status_code == 204
        assert (await h.client.post("/api/desktop/quit", headers=CSRF)).status_code == 204
    async with harness(tmp_path, repo) as h:
        assert (await h.client.post("/api/desktop/show", headers=CSRF)).status_code == 404
    assert desktop.calls == ["show", "quit"]


async def test_connect_mac_from_settings(tmp_path: Path, repo: SqliteRepository) -> None:
    async with harness(tmp_path, repo) as h:
        wrong = await h.client.put(
            "/api/settings/mac", json={"url": "mac.test", "password": "nope"}, headers=CSRF
        )
        assert wrong.status_code == 400
        assert wrong.json()["detail"] == "The Mac rejected that password."
        ok = await h.client.put("/api/settings/mac", json={"url": "mac.test", "password": "pw"}, headers=CSRF)
        assert ok.status_code == 200
        assert ok.json()["mode"] == "mac"
        assert (await h.client.get("/api/settings")).json()["mac"] == {"url": "http://mac.test:1234"}
        back = await h.client.delete("/api/settings/mac", headers=CSRF)
        assert back.json()["mode"] == "phone"


async def test_mac_mode_chats_threads_and_attachments(tmp_path: Path, repo: SqliteRepository) -> None:
    async with harness(tmp_path, repo, mac=True) as h:
        chats = (await h.client.get("/api/chats")).json()
        assert {c["id"] for c in chats} == {GROUP, DM}
        page = (await h.client.get(f"/api/chats/{path_id(GROUP)}/messages")).json()
        assert [m["id"] for m in page["messages"]] == ["g1", "g2", "g4", "g5", "g6"]
        photo = await h.client.get(page["messages"][-1]["attachments"][0]["thumb_url"])
        assert (photo.status_code, photo.headers["content-type"], photo.content) == (
            200,
            "image/jpeg",
            b"\xff\xd8jpeg",
        )


async def test_mac_mode_actions_reach_the_mac(tmp_path: Path, repo: SqliteRepository) -> None:
    async with harness(tmp_path, repo, mac=True) as h:
        react = await h.client.post(
            "/api/messages/g1/reactions", json={"chat_id": GROUP, "type": "laugh"}, headers=CSRF
        )
        typing = await h.client.post(f"/api/chats/{path_id(DM)}/typing", json={"typing": True}, headers=CSRF)
        assert (react.status_code, typing.status_code) == (202, 204)
        assert (
            "POST",
            "/message/react",
            {"chatGuid": GROUP, "selectedMessageGuid": "g1", "reaction": "laugh", "partIndex": 0},
        ) in h.server.requests
        assert ("POST", f"/chat/{DM}/typing", None) in h.server.requests


async def test_mac_mode_upload_is_queued(tmp_path: Path, repo: SqliteRepository) -> None:
    async with harness(tmp_path, repo, mac=True) as h:
        response = await h.client.post(
            f"/api/chats/{path_id(DM)}/attachments",
            files={"file": ("photo.png", b"\x89PNG", "image/png")},
            data={"temp_id": "up1"},
            headers=CSRF,
        )
        assert response.status_code == 202
        body = response.json()
        assert (body["id"], body["status"], body["attachments"][0]["name"]) == (
            "temp:up1",
            "sending",
            "photo.png",
        )


async def test_answering_a_call_opens_facetime_locally(tmp_path: Path, repo: SqliteRepository) -> None:
    async with harness(tmp_path, repo, mac=True) as h:
        response = await h.client.post("/api/calls/call-1/answer", headers=CSRF)
        assert response.json() == {"link": "https://facetime.apple.com/join#v=1&p=abc", "opened": True}
        assert h.opened == ["https://facetime.apple.com/join#v=1&p=abc"]


async def test_direct_call_opens_on_this_pc_when_ready(tmp_path: Path, repo: SqliteRepository) -> None:
    helper = FakeCallHelper()
    async with harness(tmp_path, repo, mac=True, calls=helper) as h:
        started = await h.client.post("/api/calls", json={"chat_id": DM, "video": False}, headers=CSRF)
        early = await h.client.post("/api/calls/call-1/join", headers=CSRF)
        helper.move_to(CallPhase.WAITING_FOR_PC, link=LINK)
        await h.sink.wait_for(lambda u: isinstance(u, CallUpdate) and u.call.status is CallStatus.READY)
        joined = await h.client.post("/api/calls/call-1/join", headers=CSRF)
        again = await h.client.post("/api/calls/call-1/join", headers=CSRF)
    body = started.json()
    assert (started.status_code, body["status"], body["outgoing"], body["is_video"]) == (
        200,
        "outgoing",
        True,
        False,
    )
    assert early.status_code == 400
    assert joined.json() == again.json() == {"link": LINK, "opened": True}
    assert h.opened == [LINK]


async def test_contact_photo_route(tmp_path: Path, repo: SqliteRepository) -> None:
    server = FakeBlueBubbles()
    server.contacts[0]["avatar"] = base64.b64encode(b"\xff\xd8maya").decode()
    async with harness(tmp_path, repo, mac=True, server=server) as h:
        chat = (await h.client.get(f"/api/chats/{path_id(DM)}")).json()
        photo = await h.client.get(chat["participants"][0]["photo"])
        missing = await h.client.get("/api/photos/unknown")
    assert (photo.status_code, photo.headers["content-type"], photo.content) == (
        200,
        "image/jpeg",
        b"\xff\xd8maya",
    )
    assert "immutable" in photo.headers["cache-control"]
    assert missing.status_code == 404
