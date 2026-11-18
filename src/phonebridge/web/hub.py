from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

from starlette.websockets import WebSocketDisconnect

if TYPE_CHECKING:
    from phonebridge.domain.ui import UiUpdate

logger = logging.getLogger(__name__)


class JsonSocket(Protocol):
    async def send_json(self, data: object, /) -> None: ...


class WebSocketHub:
    """Fans UI events out to every open browser window."""

    def __init__(self) -> None:
        self._clients: set[JsonSocket] = set()
        # Focused windows and the conversation each one shows (None: list or empty view).
        self._focused: dict[JsonSocket, str | None] = {}

    def is_viewing(self, chat_id: str) -> bool:
        """True while a focused window has this conversation open in front of the user."""
        return chat_id in self._focused.values()

    def attach(self, websocket: JsonSocket) -> None:
        self._clients.add(websocket)

    def detach(self, websocket: JsonSocket) -> None:
        self._clients.discard(websocket)
        self._focused.pop(websocket, None)

    def set_focus(self, websocket: JsonSocket, *, focused: bool, chat_id: str | None = None) -> None:
        if focused and websocket in self._clients:
            self._focused[websocket] = chat_id
        else:
            self._focused.pop(websocket, None)

    async def publish(self, update: UiUpdate) -> None:
        payload = update.model_dump(mode="json")
        for websocket in list(self._clients):
            try:
                await websocket.send_json(payload)
            except (WebSocketDisconnect, RuntimeError, ConnectionError) as exc:
                logger.debug("dropping closed UI socket: %s", exc)
                self.detach(websocket)
