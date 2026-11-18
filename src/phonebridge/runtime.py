"""Builds every backend component and runs the web server plus the active messaging backend.

Shared by the command line (`python -m phonebridge`) and the desktop app, which
runs it on a background thread next to its window and tray icon.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING, Protocol

import uvicorn

from phonebridge.config import ConfigStore
from phonebridge.desktop import open_in_edge
from phonebridge.domain.ui import Mode
from phonebridge.mac.call_helper import CallHelperClient, helper_url
from phonebridge.mac.client import BlueBubblesClient
from phonebridge.mac.events import SocketIoEvents
from phonebridge.services.mac_service import MacService
from phonebridge.services.manager import ServiceManager
from phonebridge.services.phone_service import PhoneService
from phonebridge.storage import SqliteRepository
from phonebridge.web import WebSocketHub, create_app

if TYPE_CHECKING:
    import socket
    from collections.abc import Sequence

    from phonebridge.config import UserConfig
    from phonebridge.domain.ui import UiUpdate
    from phonebridge.services.messaging import MessagingService, UiSink
    from phonebridge.settings import Settings

logger = logging.getLogger(__name__)


def configure_logging(settings: Settings) -> None:
    handlers: list[logging.Handler] = [
        RotatingFileHandler(
            settings.data_dir / "phonebridge.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8"
        )
    ]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers
    )
    quiet_http_logs()


def quiet_http_logs() -> None:
    # BlueBubbles takes its password in the query string, and httpx logs full request
    # URLs at INFO: keep those loggers at WARNING so the password never reaches the log.
    for name in ("httpx", "httpcore", "socketio", "engineio"):
        logging.getLogger(name).setLevel(logging.WARNING)


class DesktopHooks(Protocol):
    """Window controls the API can trigger, e.g. when the app is opened twice."""

    def show_window(self) -> None: ...

    def quit_app(self) -> None: ...


class FanoutSink:
    def __init__(self, sinks: Sequence[UiSink]) -> None:
        self._sinks = list(sinks)

    async def publish(self, update: UiUpdate) -> None:
        for sink in self._sinks:
            await sink.publish(update)


class _Server(uvicorn.Server):
    """uvicorn server that signals once it accepts connections."""

    def __init__(self, config: uvicorn.Config, ready: threading.Event) -> None:
        super().__init__(config)
        self._ready = ready

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets)
        self._ready.set()


class Runtime:
    def __init__(
        self,
        settings: Settings,
        *,
        extra_sinks: Sequence[UiSink] = (),
        desktop: DesktopHooks | None = None,
    ) -> None:
        self.settings = settings
        self.hub = WebSocketHub()
        self.ready = threading.Event()
        self._sink = FanoutSink([self.hub, *extra_sinks])
        self._desktop = desktop
        self._server: _Server | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._repo: SqliteRepository | None = None

    def _build_service(self, config: UserConfig) -> MessagingService:
        settings, repo = self.settings, self._repo
        if repo is None:
            raise RuntimeError("repository not open")
        if config.mac is not None:
            url, password = config.mac.url, config.mac.password.get_secret_value()
            return MacService(
                BlueBubblesClient(url, password),
                lambda: SocketIoEvents(url, password),
                self._sink,
                calls=CallHelperClient(helper_url(url), password),
            )
        # Demo and WinRT adapters load only when used: WinRT exists only on Windows.
        if settings.demo:
            from phonebridge.demo import DemoConnector

            return PhoneService(DemoConnector(), repo, self._sink, mode=Mode.DEMO)
        from phonebridge.bluetooth import WindowsPhoneLink, WinRtPhoneConnector

        local_tz = datetime.now(UTC).astimezone().tzinfo or UTC
        return PhoneService(
            WinRtPhoneConnector(address=settings.phone_address, local_tz=local_tz),
            repo,
            self._sink,
            phone_link=WindowsPhoneLink(),
            poll_seconds=settings.poll_seconds,
            push_poll_seconds=settings.push_poll_seconds,
        )

    async def serve(self) -> None:
        settings = self.settings
        self._loop = asyncio.get_running_loop()
        self._repo = await SqliteRepository.open(":memory:" if settings.demo else settings.database_path)
        if settings.demo:
            from phonebridge.demo import seed_demo_history

            await seed_demo_history(self._repo)
        manager = ServiceManager(
            ConfigStore(settings.data_dir / "config.json"), self._build_service, self._sink
        )
        await manager.start()
        token = settings.token.get_secret_value() if settings.token else None
        app = create_app(
            manager,
            self.hub,
            token=token,
            allowed_hosts=settings.allowed_hosts(),
            desktop=self._desktop,
            open_link=open_in_edge,
        )
        # log_config=None: our root logger already writes to the log file; uvicorn's own
        # console config breaks under pythonw.exe and frozen apps, where there is no console.
        self._server = _Server(
            uvicorn.Config(
                app,
                host=settings.host,
                port=settings.port,
                log_level="warning",
                log_config=None,
                lifespan="off",
            ),
            self.ready,
        )
        logger.info("Phonebridge running at %s", settings.url)
        try:
            async with asyncio.TaskGroup() as tg:
                backend = tg.create_task(manager.run())
                await self._server.serve()
                backend.cancel()
        finally:
            await manager.current.close()
            await self._repo.close()

    def request_stop(self) -> None:
        """Thread-safe: ask the server to finish; serve() then returns."""
        server, loop = self._server, self._loop
        if server is None or loop is None:
            return
        loop.call_soon_threadsafe(setattr, server, "should_exit", True)
