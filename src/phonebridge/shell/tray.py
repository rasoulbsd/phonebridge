from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pystray

from phonebridge.shell import registry

if TYPE_CHECKING:
    from collections.abc import Callable

    from PIL.Image import Image


class Tray:
    def __init__(self, image: Image, *, on_open: Callable[[], None], on_quit: Callable[[], None]) -> None:
        self._on_open = on_open
        self._on_quit = on_quit
        menu = pystray.Menu(
            pystray.MenuItem(f"Open {registry.DISPLAY_NAME}", self._open, default=True),
            pystray.MenuItem(
                "Start with Windows",
                self._toggle_autostart,
                checked=lambda _item: registry.autostart_enabled(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._quit),
        )
        self._icon = pystray.Icon(registry.APP_NAME, image, registry.DISPLAY_NAME, menu)

    def start(self) -> None:
        threading.Thread(target=self._icon.run, name="tray", daemon=True).start()

    def stop(self) -> None:
        self._icon.stop()

    def _open(self, _icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        self._on_open()

    def _quit(self, _icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        self._on_quit()

    def _toggle_autostart(self, _icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        registry.set_autostart(enabled=not registry.autostart_enabled())
