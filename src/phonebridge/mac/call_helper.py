"""Client for Phonebridge's FaceTime helper, which runs inside FaceTime on the Mac.

BlueBubbles can only send FaceTime links. The helper (packaging/mac/facetime-helper) rings
people from the Mac and, once they answer, hands back a link this PC opens to take over the call.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

HELPER_PORT = 1235
_TIMEOUT = httpx.Timeout(10.0, connect=3.0)
_HEALTH_TIMEOUT = httpx.Timeout(3.0, connect=2.0)
_HTTP_UNAUTHORIZED = 401
_HTTP_ERROR = 400


class CallPhase(StrEnum):
    CALLING = "calling"
    ANSWERED = "answered"
    WAITING_FOR_PC = "waiting_for_pc"
    JOINING = "joining"
    ON_PC = "on_pc"
    ENDED = "ended"
    FAILED = "failed"


class HelperCall(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str
    phase: CallPhase
    link: str | None = None
    reason: str | None = None


class CallHelperError(Exception):
    """The helper answered but refused or failed the request."""


class CallHelperUnavailableError(CallHelperError):
    """The helper didn't answer: not installed, FaceTime not running, or the Mac is unreachable."""


def helper_url(server_url: str) -> str:
    """The helper listens on the same Mac as BlueBubbles, on its own port."""
    host = urlsplit(server_url).hostname or "localhost"
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{HELPER_PORT}"


class CallHelperClient:
    def __init__(
        self, base_url: str, password: str, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=_TIMEOUT,
            transport=transport,
            headers={"X-Phonebridge-Token": password},
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def available(self) -> bool:
        try:
            await self._request("GET", "/health", http_timeout=_HEALTH_TIMEOUT)
        except CallHelperError:
            return False
        return True

    async def dial(self, handle: str, *, video: bool) -> HelperCall:
        return self._call(await self._request("POST", "/calls", body={"handle": handle, "video": video}))

    async def call(self, call_id: str) -> HelperCall:
        return self._call(await self._request("GET", f"/calls/{quote(call_id, safe='')}"))

    async def end(self, call_id: str) -> HelperCall:
        return self._call(await self._request("POST", f"/calls/{quote(call_id, safe='')}/end"))

    @staticmethod
    def _call(data: object) -> HelperCall:
        try:
            return HelperCall.model_validate(data)
        except ValidationError as exc:
            raise CallHelperError("Unexpected reply from the Mac's FaceTime helper.") from exc

    async def _request(
        self, method: str, path: str, *, body: object = None, http_timeout: httpx.Timeout = _TIMEOUT
    ) -> object:
        try:
            response = await self._http.request(method, path, json=body, timeout=http_timeout)
        except httpx.HTTPError as exc:
            raise CallHelperUnavailableError("The Mac's FaceTime helper isn't reachable.") from exc
        if response.status_code == _HTTP_UNAUTHORIZED:
            raise CallHelperUnavailableError("The Mac's FaceTime helper rejected the password.")
        try:
            raw: Any = response.json()
        except json.JSONDecodeError as exc:
            raise CallHelperError(
                f"Unexpected reply from the FaceTime helper (HTTP {response.status_code})."
            ) from exc
        if response.status_code >= _HTTP_ERROR:
            error = raw.get("error") if isinstance(raw, dict) else None
            raise CallHelperError(str(error or f"HTTP {response.status_code}"))
        return raw
