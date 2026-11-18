"""In-memory stand-in for BlueBubbles Server, served through httpx.MockTransport."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, cast

import httpx

from phonebridge.mac.events import DISCONNECTED

if TYPE_CHECKING:
    from collections.abc import Callable

BASE = 1_798_000_000_000  # epoch ms, Nov 2026
PASSWORD = "pw"
GROUP = "iMessage;+;chat-trip"
DM = "iMessage;-;+15550142"
MAYA: dict[str, object] = {"address": "+15550142", "service": "iMessage", "originalROWID": 1}
SAM: dict[str, object] = {"address": "+15550193", "service": "iMessage", "originalROWID": 2}
JORDAN: dict[str, object] = {"address": "jordan@example.com", "service": "iMessage", "originalROWID": 3}


def message(
    guid: str,
    chat: str,
    *,
    text: str | None = "hi",
    at: int = 0,
    sender: dict[str, object] | None = None,
    **extra: object,
) -> dict[str, object]:
    return {
        "guid": guid,
        "text": text,
        "handle": sender,
        "isFromMe": sender is None,
        "dateCreated": BASE + at * 60_000,
        "attachments": [],
        "chats": [{"guid": chat}],
        **extra,
    }


def created(row: dict[str, object]) -> int:
    return int(str(row["dateCreated"]))


class FakeBlueBubbles:
    def __init__(self, *, private_api: bool = True, os_version: str = "15.5") -> None:
        self.password = PASSWORD
        self.info: dict[str, object] = {
            "computer_id": "user@Test-MacBook",
            "os_version": os_version,
            "server_version": "1.9.12",
            "private_api": private_api,
            "helper_connected": private_api,
        }
        self.contacts = [
            {"displayName": "Maya Chen", "phoneNumbers": [{"address": "+1 (555) 014-2"}], "emails": []},
            {
                "firstName": "Sam",
                "lastName": "Okafor",
                "phoneNumbers": [{"address": "+15550193"}],
                "emails": [],
            },
        ]
        self.chats: dict[str, dict[str, object]] = {
            GROUP: {"guid": GROUP, "displayName": "", "style": 43, "participants": [MAYA, SAM, JORDAN]},
            DM: {"guid": DM, "displayName": None, "style": 45, "participants": [MAYA]},
        }
        self.messages: dict[str, list[dict[str, object]]] = {
            GROUP: [
                message("g1", GROUP, text="Who's in for Saturday?", at=1, sender=MAYA),
                message("g2", GROUP, text="Me!", at=2, sender=SAM, threadOriginatorGuid="g1"),
                message(
                    "g3",
                    GROUP,
                    text=None,
                    at=3,
                    sender=SAM,
                    associatedMessageGuid="p:0/g1",
                    associatedMessageType="love",
                ),
                message("g4", GROUP, text=None, at=4, sender=JORDAN, itemType=2, groupTitle="Trip"),
                message("g5", GROUP, text="old draft", at=5, dateRetracted=BASE + 6 * 60_000),
                message(
                    "g6",
                    GROUP,
                    text="￼",
                    at=7,
                    sender=MAYA,
                    attachments=[
                        {
                            "guid": "att1",
                            "mimeType": "image/jpeg",
                            "transferName": "IMG_1.jpg",
                            "totalBytes": 2048,
                            "width": 400,
                            "height": 300,
                        }
                    ],
                ),
            ],
            DM: [message("d1", DM, text="See you at 7", at=8, sender=MAYA)],
        }
        self.unread: dict[str, int] = {DM: 1}
        self.requests: list[tuple[str, str, object]] = []
        self.fail_send = False
        # Lets a test answer some requests itself; return None to fall through.
        self.override: Callable[[httpx.Request], httpx.Response | None] | None = None
        self._next = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    @staticmethod
    def _ok(data: object) -> httpx.Response:
        return httpx.Response(200, json={"status": 200, "message": "Success", "data": data})

    def _chat_out(self, guid: str) -> dict[str, object]:
        chat = dict(self.chats[guid])
        rows = self.messages.get(guid, [])
        if rows:
            chat["lastMessage"] = max(rows, key=created)
        return chat

    def _new_guid(self) -> str:
        self._next += 1
        return f"sent{self._next}"

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.override is not None and (answer := self.override(request)) is not None:
            return answer
        if request.url.params.get("password") != self.password:
            return httpx.Response(401, json={"status": 401, "message": "Unauthorized"})
        path = request.url.path.removeprefix("/api/v1")
        body: object = (
            json.loads(request.content) if request.headers.get("content-type") == "application/json" else None
        )
        self.requests.append((request.method, path, body))
        parts = path.strip("/").split("/")
        match request.method, parts:
            case "GET", ["server", "info"]:
                return self._ok(self.info)
            case "GET", ["contact"]:
                return self._ok(self.contacts)
            case "POST", ["chat", "query"]:
                return self._ok([self._chat_out(g) for g in self.chats])
            case "GET", ["chat", guid]:
                return self._ok(self._chat_out(guid)) if guid in self.chats else httpx.Response(404, json={})
            case "GET", ["chat", guid, "message"]:
                rows = sorted(self.messages.get(guid, []), key=created, reverse=True)
                before = request.url.params.get("before")
                if before:
                    rows = [m for m in rows if created(m) < int(before)]
                return self._ok(rows[: int(request.url.params.get("limit", "100"))])
            case "GET", ["message", guid]:
                found = [m for rows in self.messages.values() for m in rows if m["guid"] == guid]
                return self._ok(found[0]) if found else httpx.Response(404, json={"message": "Not found"})
            case "POST", ["message", "query"]:
                assert isinstance(body, dict)
                if "chatGuid" in body:
                    count = self.unread.get(str(body["chatGuid"]), 0)
                    return self._ok([{"guid": f"unread{i}"} for i in range(count)])
                where = cast("list[dict[str, dict[str, str]]]", body["where"])
                needle = where[0]["args"]["text"].strip("%").lower()
                hits = [
                    m
                    for rows in self.messages.values()
                    for m in rows
                    if needle in str(m.get("text") or "").lower()
                ]
                return self._ok(hits)
            case "POST", ["message", "text"]:
                assert isinstance(body, dict)
                if self.fail_send:
                    return httpx.Response(
                        500, json={"status": 500, "error": {"message": "Message failed to send"}}
                    )
                chat = str(body["chatGuid"])
                sent = message(
                    self._new_guid(), chat, text=str(body["message"]), at=60, tempGuid=body["tempGuid"]
                )
                self.messages.setdefault(chat, []).append(sent)
                return self._ok(sent)
            case "POST", ["message", "attachment"]:
                form = request.content.decode("latin-1")
                chat = DM if DM in form else GROUP
                sent = message(
                    self._new_guid(),
                    chat,
                    text=None,
                    at=61,
                    attachments=[
                        {"guid": f"att-{self._next}", "mimeType": "image/png", "transferName": "photo.png"}
                    ],
                )
                self.messages.setdefault(chat, []).append(sent)
                return self._ok(sent)
            case "GET", ["attachment", _guid, "download"]:
                return httpx.Response(200, content=b"\xff\xd8jpeg", headers={"content-type": "image/jpeg"})
            case "POST", ["facetime", "answer", _uuid]:
                return self._ok({"link": "https://facetime.apple.com/join#v=1&p=abc"})
            case _:
                return self._ok({})


class FakeEvents:
    """Socket stand-in: tests push (event, payload) pairs into it."""

    def __init__(self, *, fail: Callable[[], Exception] | None = None) -> None:
        self.queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
        self.connected = asyncio.Event()
        self._fail = fail

    async def connect(self) -> None:
        if self._fail is not None:
            raise self._fail()
        self.connected.set()

    async def next(self) -> tuple[str, object]:
        return await self.queue.get()

    async def close(self) -> None:
        return None

    def push(self, name: str, data: object) -> None:
        self.queue.put_nowait((name, data))

    def drop(self) -> None:
        self.queue.put_nowait((DISCONNECTED, None))
