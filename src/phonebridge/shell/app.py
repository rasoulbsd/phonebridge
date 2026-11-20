"""Desktop app: native window, tray icon and Windows notifications around the backend."""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import http.client
import json
import logging
import threading
from pathlib import Path

import webview
from PIL import Image

from phonebridge import branding
from phonebridge.desktop import port_in_use
from phonebridge.runtime import Runtime, configure_logging
from phonebridge.settings import Settings
from phonebridge.shell import install, registry
from phonebridge.shell.files import AttachmentError, AttachmentFiles, LocalServerFetcher, WindowsClipboard
from phonebridge.shell.overlay import CallOverlay
from phonebridge.shell.toasts import ToastNotifier, WinRtToasts
from phonebridge.shell.tray import Tray

logger = logging.getLogger(__name__)

ASSETS = Path(__file__).resolve().parent.parent / "assets"
_BACKEND_START_TIMEOUT = 30.0
_BACKEND_STOP_TIMEOUT = 10.0
_SIGNAL_TIMEOUT = 3.0
_OVERLAY_WIDTH = 380
_OVERLAY_HEIGHT = 84
_OVERLAY_MARGIN = 16
_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x80
_WS_EX_APPWINDOW = 0x40000


class _OverlayHandle:
    """The floating call window, which only exists once the GUI is set up."""

    def __init__(self) -> None:
        self.window: webview.Window | None = None

    def show(self) -> None:
        if self.window is not None:
            self.window.show()

    def hide(self) -> None:
        if self.window is not None:
            self.window.hide()


def _overlay_position() -> tuple[int | None, int | None]:
    """Bottom-right corner of the main screen, just above the taskbar."""
    screens = webview.screens
    if not screens:
        return None, None
    area = screens[0].frame
    try:
        right, bottom = int(area.X + area.Width), int(area.Y + area.Height)
    except AttributeError:
        right, bottom = screens[0].x + screens[0].width, screens[0].y + screens[0].height
    return right - _OVERLAY_WIDTH - _OVERLAY_MARGIN, bottom - _OVERLAY_HEIGHT - _OVERLAY_MARGIN


def _page_url(app_url: str, page: str) -> str:
    base, separator, query = app_url.partition("?")
    return f"{base.rstrip('/')}/{page}{separator}{query}"


def _keep_off_taskbar(window: webview.Window) -> None:
    """A tool window: no taskbar button of its own for the floating call window."""
    native = getattr(window, "native", None)
    if native is None:
        return
    hwnd = int(native.Handle.ToInt64())
    user32 = ctypes.windll.user32
    style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, (style | _WS_EX_TOOLWINDOW) & ~_WS_EX_APPWINDOW)


class _WindowSaveDialog:
    """Windows' own Save As dialog, owned by the main window."""

    def __init__(self) -> None:
        self.window: webview.Window | None = None

    def ask(self, suggested_name: str, directory: Path) -> Path | None:
        if self.window is None:
            return None
        chosen = self.window.create_file_dialog(
            webview.FileDialog.SAVE, directory=str(directory), save_filename=suggested_name
        )
        if not chosen:
            return None
        return Path(chosen if isinstance(chosen, str) else chosen[0])


class DesktopBridge:
    """Methods the window calls as window.pywebview.api.*; results are plain dicts for JavaScript."""

    def __init__(self, files: AttachmentFiles) -> None:
        self._files = files

    def save_attachment(self, url: str, name: str) -> dict[str, str | bool | None]:
        try:
            saved = self._files.save(url, name)
        except AttachmentError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "path": saved}

    def copy_attachment(self, url: str, name: str) -> dict[str, str | bool | None]:
        try:
            kind = self._files.copy(url, name)
        except AttachmentError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "kind": kind}


class DesktopApp:
    def __init__(self, settings: Settings, *, start_hidden: bool) -> None:
        self._settings = settings
        self._start_hidden = start_hidden
        self._window: webview.Window | None = None
        self._quitting = threading.Event()
        self._toasts = ToastNotifier(
            WinRtToasts(registry.APP_ID, self.open_conversation), is_viewing=self._user_is_viewing
        )
        self._overlay = _OverlayHandle()
        self._save_dialog = _WindowSaveDialog()
        token = settings.token.get_secret_value() if settings.token else None
        self._bridge = DesktopBridge(
            AttachmentFiles(
                LocalServerFetcher(settings.port, token),
                WindowsClipboard(),
                self._save_dialog,
                clipboard_dir=settings.data_dir / "clipboard",
                downloads_dir=Path.home() / "Downloads",
            )
        )
        self._runtime = Runtime(
            settings, extra_sinks=[self._toasts, CallOverlay(self._overlay)], desktop=self
        )
        self._tray = Tray(
            Image.open(ASSETS / branding.ICON_PNG), on_open=self.show_window, on_quit=self.quit_app
        )

    def run(self) -> None:
        backend = threading.Thread(target=self._run_backend, name="backend", daemon=True)
        backend.start()
        if not self._runtime.ready.wait(_BACKEND_START_TIMEOUT):
            install.message_box(
                "Phonebridge couldn't start. See phonebridge.log in your Phonebridge data folder."
            )
            return
        self._tray.start()
        self._window = webview.create_window(
            registry.DISPLAY_NAME,
            self._settings.url,
            width=1180,
            height=800,
            min_size=(380, 520),
            hidden=self._start_hidden,
            text_select=True,
            background_color="#111214" if registry.prefers_dark_theme() else "#ffffff",
            js_api=self._bridge,
        )
        if self._window is None:
            return
        self._save_dialog.window = self._window
        self._window.events.closing += self._on_closing
        self._create_overlay()
        # Plain links with a download attribute (and anything not routed through the bridge) still work.
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.start(
            gui="edgechromium", private_mode=False, storage_path=str(self._settings.data_dir / "webview")
        )
        self._tray.stop()
        self._runtime.request_stop()
        backend.join(_BACKEND_STOP_TIMEOUT)

    def _create_overlay(self) -> None:
        x, y = _overlay_position()
        overlay = webview.create_window(
            f"{registry.DISPLAY_NAME} call",
            _page_url(self._settings.url, "static/overlay.html"),
            width=_OVERLAY_WIDTH,
            height=_OVERLAY_HEIGHT,
            x=x,
            y=y,
            resizable=False,
            frameless=True,
            easy_drag=False,
            on_top=True,
            # Clicks work, but typing elsewhere isn't interrupted when a call comes in.
            focus=False,
            hidden=True,
            background_color="#1c1c1e",
        )
        if overlay is not None:
            overlay.events.before_show += lambda: _keep_off_taskbar(overlay)
            self._overlay.window = overlay

    def _run_backend(self) -> None:
        try:
            asyncio.run(self._runtime.serve())
        except Exception:
            # Top of the backend thread: record why it died, then take the app down with it.
            logger.exception("backend stopped unexpectedly")
            self.quit_app()

    def _user_is_viewing(self, chat_id: str) -> bool:
        return self._runtime.hub.is_viewing(chat_id)

    def _on_closing(self) -> bool:
        if self._quitting.is_set() or self._window is None:
            return True
        # Closing the window keeps Phonebridge running in the tray.
        self._window.hide()
        return False

    # DesktopHooks: called from the backend, tray and notification threads.

    def show_window(self) -> None:
        if self._window is not None:
            self._window.show()
            self._window.restore()

    def quit_app(self) -> None:
        self._quitting.set()
        if self._overlay.window is not None:
            self._overlay.window.destroy()
        if self._window is not None:
            self._window.destroy()

    def open_conversation(self, chat_id: str | None) -> None:
        self.show_window()
        if self._window is not None and chat_id:
            self._window.evaluate_js(
                f"window.phonebridge && window.phonebridge.openConversation({json.dumps(chat_id)})"
            )


def _signal_running_instance(port: int) -> None:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=_SIGNAL_TIMEOUT)
    try:
        connection.request("POST", "/api/desktop/show", headers={"X-Requested-With": "phonebridge"})
        connection.getresponse().read()
    except OSError as exc:
        logger.warning("could not reach the running Phonebridge: %s", exc)
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog=registry.DISPLAY_NAME)
    parser.add_argument("--background", action="store_true", help="start in the tray without a window")
    parser.add_argument("--demo", action="store_true", help="use a pretend phone with fictional messages")
    parser.add_argument("--port", type=int, help="local port (default 8765)")
    parser.add_argument("--uninstall", action="store_true", help="remove Phonebridge from this PC")
    args = parser.parse_args(argv)

    overrides: dict[str, object] = {"open_window": False}
    if args.demo:
        overrides["demo"] = True
    if args.port:
        overrides["port"] = args.port
    settings = Settings.model_validate(overrides)

    if args.uninstall:
        install.uninstall(settings.data_dir, port=settings.port)
        return
    if port_in_use(settings.port):
        if not args.background:
            _signal_running_instance(settings.port)
        return
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    configure_logging(settings)
    # Groups the window, taskbar button and notifications under one app identity.
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(registry.APP_ID)
    registry.register_notifications(ASSETS / branding.ICON_PNG)
    DesktopApp(settings, start_hidden=args.background).run()
