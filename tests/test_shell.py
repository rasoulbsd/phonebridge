from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from phonebridge.branding import DISPLAY_NAME
from phonebridge.domain.ui import (
    AppStatus,
    Attachment,
    Call,
    CallStatus,
    CallUpdate,
    Chat,
    ChatMessage,
    Delivery,
    MessageUpdate,
    Mode,
    Person,
    State,
    StatusUpdate,
)
from phonebridge.runtime import FanoutSink
from phonebridge.shell import registry
from phonebridge.shell.overlay import CallOverlay
from phonebridge.shell.toasts import ToastNotifier, call_toast_xml, toast_xml
from phonebridge.web import WebSocketHub
from tests.conftest import NOW, UpdateRecorder, clock

if TYPE_CHECKING:
    from phonebridge.domain.ui import UiUpdate

MAYA = Person(address="+15550142", name="Maya Chen")


def message_update(
    message_id: str = "m1",
    *,
    from_me: bool = False,
    minutes_ago: int = 0,
    text: str = "hello",
    group: bool = False,
    event: str | None = None,
    attachments: list[Attachment] | None = None,
) -> MessageUpdate:
    at = NOW - timedelta(minutes=minutes_ago)
    return MessageUpdate(
        message=ChatMessage(
            id=message_id,
            chat_id="chat-1",
            from_me=from_me,
            sender=None if from_me else MAYA,
            text=text,
            at=at,
            status=Delivery.SENT if from_me else Delivery.RECEIVED,
            event=event,
            attachments=attachments or [],
        ),
        chat=Chat(id="chat-1", title="Trip" if group else "Maya Chen", is_group=group, participants=[MAYA]),
    )


class FakeShower:
    def __init__(self) -> None:
        self.shown: list[tuple[str, str, str | None]] = []
        self.calls: list[tuple[str, str, str]] = []
        self.hidden_calls: list[str] = []

    def show(self, title: str, body: str, chat_id: str | None) -> None:
        self.shown.append((title, body, chat_id))

    def show_call(self, title: str, body: str, call_id: str) -> None:
        self.calls.append((title, body, call_id))

    def hide_call(self, call_id: str) -> None:
        self.hidden_calls.append(call_id)


def test_toast_xml_escapes_untrusted_text_and_breaks_through_do_not_disturb() -> None:
    root = ET.fromstring(toast_xml('A & B <x> "q"', "1 < 2 & 3 > 2", "c'1\""))
    assert [t.text for t in root.iter("text")] == ['A & B <x> "q"', "1 < 2 & 3 > 2"]
    assert (root.get("launch"), root.get("scenario")) == ("chat=c'1\"", "urgent")


def test_call_toast_rings_and_has_a_button() -> None:
    root = ET.fromstring(call_toast_xml("FaceTime video call", "Maya <3 is calling"))
    assert root.get("scenario") == "incomingCall"
    assert [a.get("content") for a in root.iter("action")] == ["Open"]
    audio = root.find("audio")
    assert audio is not None
    assert audio.get("loop") == "true"


async def test_toast_for_fresh_incoming_message() -> None:
    shower = FakeShower()
    notifier = ToastNotifier(shower, is_viewing=lambda _chat: False, clock=clock)
    await notifier.publish(message_update(text="x" * 300))
    [(title, body, chat_id)] = shower.shown
    assert (title, chat_id, len(body)) == ("Maya Chen", "chat-1", 203)


async def test_group_toast_names_the_sender_and_photos() -> None:
    shower = FakeShower()
    notifier = ToastNotifier(shower, is_viewing=lambda _chat: False, clock=clock)
    photo = Attachment(id="a", name="IMG.jpg", mime="image/jpeg", url="/a")
    await notifier.publish(message_update(group=True, text="", attachments=[photo]))
    assert shower.shown == [("Trip", "Maya: Photo", "chat-1")]


@pytest.mark.parametrize(
    "update",
    [
        message_update(from_me=True),
        message_update(minutes_ago=10),
        message_update(event="Sam named the conversation"),
        StatusUpdate(status=AppStatus(mode=Mode.MAC, state=State.CONNECTED)),
    ],
)
async def test_no_toast_for_old_own_or_system_messages(update: UiUpdate) -> None:
    shower = FakeShower()
    await ToastNotifier(shower, is_viewing=lambda _chat: False, clock=clock).publish(update)
    assert shower.shown == []


async def test_toast_held_only_for_the_conversation_being_read() -> None:
    shower = FakeShower()
    viewing = {"chat": "chat-1"}
    notifier = ToastNotifier(shower, is_viewing=lambda chat: chat == viewing["chat"], clock=clock)
    await notifier.publish(message_update("1"))
    viewing["chat"] = "another-chat"
    await notifier.publish(message_update("2"))
    await notifier.publish(message_update("2"))
    assert [chat for _, _, chat in shower.shown] == ["chat-1"]


async def test_incoming_call_rings_once_and_clears_when_it_ends() -> None:
    shower = FakeShower()
    notifier = ToastNotifier(shower, is_viewing=lambda _chat: True, clock=clock)
    call = CallUpdate(call=Call(id="c1", status=CallStatus.INCOMING, caller=MAYA, is_video=True))
    await notifier.publish(call)
    await notifier.publish(call)
    await notifier.publish(CallUpdate(call=Call(id="c1", status=CallStatus.ENDED)))
    assert shower.calls == [
        ("FaceTime video call", f"Maya Chen is calling. Open {DISPLAY_NAME} to answer.", "c1")
    ]
    assert shower.hidden_calls == ["c1"]


async def test_fanout_sink_reaches_every_sink() -> None:
    first, second = UpdateRecorder(), UpdateRecorder()
    update = StatusUpdate(status=AppStatus(mode=Mode.PHONE, state=State.CONNECTED))
    await FanoutSink([first, second]).publish(update)
    assert first.updates == second.updates == [update]


class _Socket:
    async def send_json(self, data: object, /) -> None:
        return None


def test_hub_tracks_the_conversation_each_focused_window_shows() -> None:
    hub = WebSocketHub()
    one, two = _Socket(), _Socket()
    hub.attach(one)
    hub.attach(two)
    hub.set_focus(one, focused=True, chat_id="chat-1")
    hub.set_focus(two, focused=True)
    assert (hub.is_viewing("chat-1"), hub.is_viewing("chat-2")) == (True, False)
    hub.set_focus(one, focused=True, chat_id="chat-2")
    assert (hub.is_viewing("chat-1"), hub.is_viewing("chat-2")) == (False, True)
    hub.set_focus(one, focused=False, chat_id="chat-2")
    assert not hub.is_viewing("chat-2")
    hub.set_focus(two, focused=True, chat_id="chat-3")
    hub.detach(two)
    assert not hub.is_viewing("chat-3")
    hub.set_focus(_Socket(), focused=True, chat_id="chat-4")
    assert not hub.is_viewing("chat-4")


def test_launch_command_runs_the_shell_module() -> None:
    command = registry.launch_command("--background")
    assert command.endswith("-m phonebridge.shell --background")
    assert "pythonw.exe" in command.lower()


class FakeOverlayWindow:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.hidden = asyncio.Event()

    def show(self) -> None:
        self.events.append("show")

    def hide(self) -> None:
        self.events.append("hide")
        self.hidden.set()


def call_update(status: CallStatus, call_id: str = "c1", *, outgoing: bool = True) -> CallUpdate:
    return CallUpdate(call=Call(id=call_id, status=status, caller=MAYA, outgoing=outgoing))


async def test_floating_call_window_follows_a_placed_call() -> None:
    window = FakeOverlayWindow()
    overlay = CallOverlay(window, linger=0.01)
    for status in (CallStatus.OUTGOING, CallStatus.ANSWERED, CallStatus.READY):
        await overlay.publish(call_update(status))
    await overlay.publish(call_update(CallStatus.ENDED, "someone-else"))
    await overlay.publish(StatusUpdate(status=AppStatus(mode=Mode.MAC, state=State.CONNECTED)))
    assert window.events == ["show", "show", "show"]
    await overlay.publish(call_update(CallStatus.CONNECTED))
    async with asyncio.timeout(2):
        await window.hidden.wait()
    assert window.events == ["show", "show", "show", "hide"]


async def test_floating_call_window_stops_ringing_on_its_own() -> None:
    window = FakeOverlayWindow()
    overlay = CallOverlay(window, ring_timeout=0.01)
    await overlay.publish(call_update(CallStatus.INCOMING, outgoing=False))
    async with asyncio.timeout(2):
        await window.hidden.wait()
    assert window.events == ["show", "hide"]
