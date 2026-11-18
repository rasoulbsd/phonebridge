"""Native Windows notifications for new messages and incoming FaceTime calls.

Messages use the "urgent" scenario (Windows "important notifications"), which lets them
break through Do Not Disturb; calls use "incomingCall" so they ring and stay on screen
until the call is answered or ends.
"""

from __future__ import annotations

import collections
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol
from xml.sax.saxutils import escape, quoteattr

from winrt.windows.data.xml.dom import XmlDocument
from winrt.windows.ui.notifications import ToastNotification, ToastNotificationManager

from phonebridge.domain.ui import CallStatus, CallUpdate, Delivery, MessageUpdate
from phonebridge.shell.registry import DISPLAY_NAME

if TYPE_CHECKING:
    from collections.abc import Callable

    from phonebridge.domain.ui import UiUpdate

logger = logging.getLogger(__name__)

_BODY_LIMIT = 200
# Messages older than this are history being synced, not news.
_FRESHNESS = timedelta(minutes=2)
_REMEMBERED = 500
_CALL_GROUP = "calls"


class ToastShower(Protocol):
    def show(self, title: str, body: str, chat_id: str | None) -> None: ...

    def show_call(self, title: str, body: str, call_id: str) -> None: ...

    def hide_call(self, call_id: str) -> None: ...


def toast_xml(title: str, body: str, chat_id: str | None) -> str:
    launch = f" launch={quoteattr('chat=' + chat_id)}" if chat_id else ""
    return (
        f"<toast{launch} scenario='urgent'>"
        "<visual><binding template='ToastGeneric'>"
        f"<text>{escape(title)}</text><text>{escape(body)}</text>"
        "</binding></visual>"
        "<audio src='ms-winsoundevent:Notification.IM'/>"
        "</toast>"
    )


def call_toast_xml(title: str, body: str) -> str:
    # Windows only honours the incomingCall scenario when the toast has a button.
    return (
        "<toast launch='call' scenario='incomingCall'>"
        "<visual><binding template='ToastGeneric'>"
        f"<text>{escape(title)}</text><text>{escape(body)}</text>"
        "</binding></visual>"
        "<actions><action content='Open' arguments='call' activationType='foreground'/></actions>"
        "<audio src='ms-winsoundevent:Notification.Looping.Call' loop='true'/>"
        "</toast>"
    )


class WinRtToasts:
    def __init__(self, app_id: str, on_activate: Callable[[str | None], None]) -> None:
        self._app_id = app_id
        self._notifier = ToastNotificationManager.create_toast_notifier(app_id)
        self._on_activate = on_activate
        # WinRT drops activation callbacks for toasts that Python has garbage-collected.
        self._live: collections.deque[ToastNotification] = collections.deque(maxlen=20)
        self._calls: dict[str, ToastNotification] = {}

    def _post(self, xml: str, chat_id: str | None, *, tag: str | None = None) -> ToastNotification:
        document = XmlDocument()
        document.load_xml(xml)
        toast = ToastNotification(document)
        if tag:
            toast.tag = tag
            toast.group = _CALL_GROUP
        toast.add_activated(lambda _toast, _args: self._on_activate(chat_id))
        self._live.append(toast)
        try:
            self._notifier.show(toast)
        except OSError as exc:
            logger.warning("could not show notification: %s", exc)
        else:
            logger.info("notification shown (%s)", "call" if tag else "message")
        return toast

    def show(self, title: str, body: str, chat_id: str | None) -> None:
        self._post(toast_xml(title, body, chat_id), chat_id)

    def show_call(self, title: str, body: str, call_id: str) -> None:
        self._calls[call_id] = self._post(call_toast_xml(title, body), None, tag=call_id[-64:])

    def hide_call(self, call_id: str) -> None:
        toast = self._calls.pop(call_id, None)
        if toast is None:
            return
        try:
            self._notifier.hide(toast)
            ToastNotificationManager.history.remove(call_id[-64:], _CALL_GROUP, self._app_id)
        except OSError as exc:
            logger.info("could not clear the call notification: %s", exc)


def _body(update: MessageUpdate) -> str:
    message = update.message
    text = message.text
    if not text and message.attachments:
        text = "Photo" if message.attachments[0].mime.startswith("image/") else "Attachment"
    if len(text) > _BODY_LIMIT:
        text = text[:_BODY_LIMIT] + "..."
    if update.chat.is_group and message.sender:
        who = message.sender.name or message.sender.address
        return f"{who.split()[0]}: {text}"
    return text


class ToastNotifier:
    """UI sink that shows a toast for new incoming messages and calls.

    A message is held back only while its own conversation is open in a focused window;
    messages in any other conversation still pop up.
    """

    def __init__(
        self,
        shower: ToastShower,
        *,
        is_viewing: Callable[[str], bool],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._shower = shower
        self._is_viewing = is_viewing
        self._clock = clock
        self._seen: collections.deque[str] = collections.deque(maxlen=_REMEMBERED)

    async def publish(self, update: UiUpdate) -> None:
        if isinstance(update, CallUpdate):
            self._on_call(update)
            return
        if not isinstance(update, MessageUpdate):
            return
        message = update.message
        if (
            message.from_me
            or message.status is not Delivery.RECEIVED
            or message.event
            or message.id in self._seen
            or self._clock() - message.at > _FRESHNESS
            or self._is_viewing(message.chat_id)
        ):
            return
        self._seen.append(message.id)
        self._shower.show(update.chat.title, _body(update) or "New message", update.chat.id)

    def _on_call(self, update: CallUpdate) -> None:
        call = update.call
        if call.status is CallStatus.INCOMING:
            if call.id in self._seen:
                return
            self._seen.append(call.id)
            who = (call.caller.name or call.caller.address) if call.caller else "Someone"
            kind = "video" if call.is_video else "audio"
            self._shower.show_call(
                f"FaceTime {kind} call", f"{who} is calling. Open {DISPLAY_NAME} to answer.", call.id
            )
        else:
            self._shower.hide_call(call.id)
