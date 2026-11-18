"""Floating call window: stays above every other window while a call rings or moves to the PC."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol

from phonebridge.domain.ui import CallStatus, CallUpdate

if TYPE_CHECKING:
    from phonebridge.domain.ui import UiUpdate

_ACTIVE = frozenset(
    {CallStatus.INCOMING, CallStatus.RINGING, CallStatus.OUTGOING, CallStatus.ANSWERED, CallStatus.READY}
)
# Long enough to read "Call ended" or "On this PC" before the window goes away.
_LINGER = 3.0
# Incoming calls that never report an end (BlueBubbles without FaceTime calling) stop ringing here.
_RING_TIMEOUT = 90.0


class OverlayWindow(Protocol):
    def show(self) -> None: ...

    def hide(self) -> None: ...


class CallOverlay:
    """UI sink that shows the floating call window for the current call and hides it afterwards."""

    def __init__(
        self, window: OverlayWindow, *, linger: float = _LINGER, ring_timeout: float = _RING_TIMEOUT
    ) -> None:
        self._window = window
        self._linger = linger
        self._ring_timeout = ring_timeout
        self._call_id: str | None = None
        self._hide_later: asyncio.TimerHandle | None = None

    async def publish(self, update: UiUpdate) -> None:
        if not isinstance(update, CallUpdate):
            return
        call = update.call
        if call.status in _ACTIVE:
            self._call_id = call.id
            self._schedule_hide(self._ring_timeout if call.status is CallStatus.INCOMING else None)
            await asyncio.to_thread(self._window.show)
        elif call.id == self._call_id:
            self._schedule_hide(self._linger)

    def _schedule_hide(self, delay: float | None) -> None:
        if self._hide_later is not None:
            self._hide_later.cancel()
            self._hide_later = None
        if delay is not None:
            self._hide_later = asyncio.get_running_loop().call_later(delay, self._hide)

    def _hide(self) -> None:
        self._hide_later = None
        self._call_id = None
        self._window.hide()
