"""REST client for BlueBubbles Server v1 running on the user's Mac."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, TypeAdapter, ValidationError

from phonebridge.mac.models import (
    BBChat,
    BBContact,
    BBLink,
    BBMessage,
    BBServerInfo,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(20.0, connect=5.0)
# Sending waits for Messages.app to actually send, which can take a while.
_SEND_TIMEOUT = httpx.Timeout(90.0, connect=5.0)
_HTTP_UNAUTHORIZED = 401
_HTTP_ERROR = 400
_THUMB_WIDTH = 480

_CHATS = TypeAdapter(list[BBChat])
_MESSAGES = TypeAdapter(list[BBMessage])
_CONTACTS = TypeAdapter(list[BBContact])


class BlueBubblesError(Exception):
    """The Mac answered but refused or failed the request."""


class BlueBubblesAuthError(BlueBubblesError):
    pass


class BlueBubblesUnavailableError(BlueBubblesError):
    """The Mac didn't answer: off, asleep, wrong address, or the server isn't running."""


def _parse[M: BaseModel](model: type[M], data: object) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise BlueBubblesError("Unexpected reply from the Mac.") from exc


def _parse_list[T](adapter: TypeAdapter[list[T]], data: object) -> list[T]:
    try:
        return adapter.validate_python(data)
    except ValidationError as exc:
        raise BlueBubblesError("Unexpected reply from the Mac.") from exc


def _segment(value: str) -> str:
    return quote(value, safe="")


class BlueBubblesClient:
    def __init__(
        self, base_url: str, password: str, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._password = password
        self._http = httpx.AsyncClient(
            base_url=f"{self.base_url}/api/v1", timeout=_TIMEOUT, transport=transport, follow_redirects=True
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
        body: object = None,
        files: Mapping[str, tuple[str, bytes, str]] | None = None,
        data: Mapping[str, str] | None = None,
        http_timeout: httpx.Timeout = _TIMEOUT,
    ) -> object:
        """Returns the envelope's `data` (untrusted JSON; callers validate it)."""
        query: dict[str, str | int] = {"password": self._password, **(params or {})}
        try:
            response = await self._http.request(
                method,
                path,
                params=query,
                json=body if files is None else None,
                files=files,
                data=data,
                timeout=http_timeout,
            )
        except httpx.HTTPError as exc:
            raise BlueBubblesUnavailableError(f"Can't reach the Mac at {self.base_url}.") from exc
        if response.status_code == _HTTP_UNAUTHORIZED:
            raise BlueBubblesAuthError("The Mac rejected the password.")
        try:
            raw: Any = response.json()
        except json.JSONDecodeError as exc:
            raise BlueBubblesError(f"Unexpected reply from the Mac (HTTP {response.status_code}).") from exc
        if response.status_code >= _HTTP_ERROR or not isinstance(raw, dict):
            error = raw.get("error") if isinstance(raw, dict) else None
            detail = error.get("message") if isinstance(error, dict) else None
            message = raw.get("message") if isinstance(raw, dict) else None
            raise BlueBubblesError(str(detail or message or f"HTTP {response.status_code}"))
        return raw.get("data")

    # Server

    async def server_info(self) -> BBServerInfo:
        return _parse(BBServerInfo, await self.request("GET", "/server/info"))

    # Chats and messages

    async def chats(self, *, limit: int = 1000) -> list[BBChat]:
        data = await self.request(
            "POST",
            "/chat/query",
            body={
                "with": ["lastMessage", "participants"],
                "sort": "lastmessage",
                "limit": limit,
                "offset": 0,
            },
        )
        return _parse_list(_CHATS, data)

    async def chat(self, guid: str) -> BBChat:
        data = await self.request(
            "GET", f"/chat/{_segment(guid)}", params={"with": "participants,lastmessage"}
        )
        return _parse(BBChat, data)

    async def chat_messages(self, guid: str, *, before_ms: int | None, limit: int) -> list[BBMessage]:
        params: dict[str, str | int] = {"with": "attachment,handle", "sort": "DESC", "limit": limit}
        if before_ms is not None:
            params["before"] = before_ms
        data = await self.request("GET", f"/chat/{_segment(guid)}/message", params=params)
        return _parse_list(_MESSAGES, data)

    async def message(self, guid: str) -> BBMessage:
        data = await self.request(
            "GET", f"/message/{_segment(guid)}", params={"with": "chats,attachments,handle"}
        )
        return _parse(BBMessage, data)

    async def unread_count(self, chat_guid: str) -> int:
        data = await self.request(
            "POST",
            "/message/query",
            body={
                "chatGuid": chat_guid,
                "where": [{"statement": "message.is_from_me = 0 AND message.is_read = 0", "args": {}}],
                "limit": 100,
            },
        )
        # Only the count matters here, so the rows aren't validated one by one.
        return len(data) if isinstance(data, list) else 0

    async def search(self, text: str, *, limit: int) -> list[BBMessage]:
        data = await self.request(
            "POST",
            "/message/query",
            body={
                "with": ["chat", "chat.participants", "attachment", "handle"],
                "where": [{"statement": "message.text LIKE :text", "args": {"text": f"%{text}%"}}],
                "sort": "DESC",
                "limit": limit,
            },
        )
        return _parse_list(_MESSAGES, data)

    # Sending

    async def send_text(
        self, chat_guid: str, text: str, *, temp_guid: str, method: str, reply_to: str | None = None
    ) -> BBMessage:
        body: dict[str, object] = {
            "chatGuid": chat_guid,
            "tempGuid": temp_guid,
            "message": text,
            "method": method,
        }
        if reply_to:
            body["selectedMessageGuid"] = reply_to
            body["partIndex"] = 0
        data = await self.request("POST", "/message/text", body=body, http_timeout=_SEND_TIMEOUT)
        return _parse(BBMessage, data)

    async def send_attachment(
        self, chat_guid: str, *, temp_guid: str, filename: str, content: bytes, content_type: str, method: str
    ) -> BBMessage:
        data = await self.request(
            "POST",
            "/message/attachment",
            files={"attachment": (filename, content, content_type)},
            data={"chatGuid": chat_guid, "tempGuid": temp_guid, "name": filename, "method": method},
            http_timeout=_SEND_TIMEOUT,
        )
        return _parse(BBMessage, data)

    async def new_chat(self, addresses: Sequence[str], text: str, *, temp_guid: str, method: str) -> BBChat:
        data = await self.request(
            "POST",
            "/chat/new",
            body={
                "addresses": list(addresses),
                "message": text,
                "method": method,
                "service": "iMessage",
                "tempGuid": temp_guid,
            },
            http_timeout=_SEND_TIMEOUT,
        )
        return _parse(BBChat, data)

    # Private API actions

    async def mark_read(self, chat_guid: str) -> None:
        await self.request("POST", f"/chat/{_segment(chat_guid)}/read")

    async def set_typing(self, chat_guid: str, *, typing: bool) -> None:
        await self.request("POST" if typing else "DELETE", f"/chat/{_segment(chat_guid)}/typing")

    async def rename(self, chat_guid: str, title: str) -> BBChat:
        data = await self.request("PUT", f"/chat/{_segment(chat_guid)}", body={"displayName": title})
        return _parse(BBChat, data)

    async def add_participant(self, chat_guid: str, address: str) -> BBChat:
        data = await self.request(
            "POST", f"/chat/{_segment(chat_guid)}/participant", body={"address": address}
        )
        return _parse(BBChat, data)

    async def remove_participant(self, chat_guid: str, address: str) -> BBChat:
        data = await self.request(
            "DELETE", f"/chat/{_segment(chat_guid)}/participant", body={"address": address}
        )
        return _parse(BBChat, data)

    async def leave(self, chat_guid: str) -> None:
        await self.request("POST", f"/chat/{_segment(chat_guid)}/leave")

    async def react(self, chat_guid: str, message_guid: str, reaction: str) -> None:
        await self.request(
            "POST",
            "/message/react",
            body={
                "chatGuid": chat_guid,
                "selectedMessageGuid": message_guid,
                "reaction": reaction,
                "partIndex": 0,
            },
            http_timeout=_SEND_TIMEOUT,
        )

    async def edit(self, message_guid: str, text: str) -> None:
        await self.request(
            "POST",
            f"/message/{_segment(message_guid)}/edit",
            body={
                "editedMessage": text,
                "backwardsCompatibilityMessage": f"Edited to: {text}",
                "partIndex": 0,
            },
        )

    async def unsend(self, message_guid: str) -> None:
        await self.request("POST", f"/message/{_segment(message_guid)}/unsend", body={"partIndex": 0})

    # Contacts

    async def contacts(self) -> list[BBContact]:
        # BlueBubbles 1.9 only includes photos for "avatar", and sends them full size.
        data = await self.request("GET", "/contact", params={"extraProperties": "avatar"})
        return _parse_list(_CONTACTS, data)

    # FaceTime

    async def facetime_answer(self, call_uuid: str) -> str:
        data = await self.request(
            "POST", f"/facetime/answer/{_segment(call_uuid)}", http_timeout=_SEND_TIMEOUT
        )
        return _parse(BBLink, data).link

    async def facetime_leave(self, call_uuid: str) -> None:
        await self.request("POST", f"/facetime/leave/{_segment(call_uuid)}")

    # Attachments

    async def open_attachment(self, guid: str, *, thumb: bool) -> httpx.Response:
        """Streams a file; the caller must `aclose()` the response."""
        params: dict[str, str | int] = {"password": self._password}
        if thumb:
            params.update({"width": _THUMB_WIDTH, "quality": "good"})
        request = self._http.build_request("GET", f"/attachment/{_segment(guid)}/download", params=params)
        try:
            response = await self._http.send(request, stream=True)
        except httpx.HTTPError as exc:
            raise BlueBubblesUnavailableError(f"Can't reach the Mac at {self.base_url}.") from exc
        if response.status_code >= _HTTP_ERROR:
            await response.aclose()
            if response.status_code == _HTTP_UNAUTHORIZED:
                raise BlueBubblesAuthError("The Mac rejected the password.")
            raise BlueBubblesError(f"Attachment unavailable (HTTP {response.status_code}).")
        return response
