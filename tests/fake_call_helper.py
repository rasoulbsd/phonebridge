"""In-memory stand-in for the Mac's FaceTime helper (the CallHelper protocol)."""

from __future__ import annotations

from phonebridge.mac.call_helper import CallHelperError, CallPhase, HelperCall

LINK = "https://facetime.apple.com/join#v=1&p=direct"


class FakeCallHelper:
    def __init__(self, *, available: bool = True, dial_error: str | None = None) -> None:
        self.is_available = available
        self.dial_error = dial_error
        self.dialed: list[tuple[str, bool]] = []
        self.ended: list[str] = []
        # What the next polls report; tests move it along to play out a call.
        self.state: HelperCall | None = None
        self.closed = False

    async def available(self) -> bool:
        return self.is_available

    async def dial(self, handle: str, *, video: bool) -> HelperCall:
        if self.dial_error:
            raise CallHelperError(self.dial_error)
        self.dialed.append((handle, video))
        self.state = HelperCall(id="call-1", phase=CallPhase.CALLING)
        return self.state

    async def call(self, call_id: str) -> HelperCall:
        if self.state is None or self.state.id != call_id:
            raise CallHelperError("No such call.")
        return self.state

    async def end(self, call_id: str) -> HelperCall:
        self.ended.append(call_id)
        self.state = HelperCall(id=call_id, phase=CallPhase.ENDED, reason="You ended the call.")
        return self.state

    async def close(self) -> None:
        self.closed = True

    def move_to(self, phase: CallPhase, *, link: str | None = None, reason: str | None = None) -> None:
        self.state = HelperCall(id="call-1", phase=phase, link=link, reason=reason)
