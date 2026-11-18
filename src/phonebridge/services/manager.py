"""Owns the active backend (Mac or iPhone) and swaps it when the user changes the connection."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from pydantic import SecretStr

from phonebridge.config import MacConnection, UserConfig
from phonebridge.domain.ui import StatusUpdate
from phonebridge.mac.client import BlueBubblesAuthError, BlueBubblesClient, BlueBubblesError
from phonebridge.services.messaging import RequestError

if TYPE_CHECKING:
    from collections.abc import Callable

    import httpx

    from phonebridge.config import ConfigStore
    from phonebridge.domain.ui import AppStatus
    from phonebridge.services.messaging import MessagingService, UiSink

logger = logging.getLogger(__name__)

DEFAULT_MAC_PORT = 1234
_SWAP_TIMEOUT = 5.0
_CRASH_RESTART_DELAY = 5.0


def normalize_mac_url(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise RequestError("Enter the Mac's address, for example http://192.168.1.20:1234")
    if "://" not in value:
        value = f"http://{value}"
    parts = urlsplit(value)
    host = parts.hostname
    if parts.scheme not in ("http", "https") or not host:
        raise RequestError("That doesn't look like an address. Try http://192.168.1.20:1234")
    if ":" in host:
        host = f"[{host}]"
    return f"{parts.scheme}://{host}:{parts.port or DEFAULT_MAC_PORT}"


class ServiceManager:
    def __init__(
        self,
        store: ConfigStore,
        factory: Callable[[UserConfig], MessagingService],
        sink: UiSink,
        *,
        probe_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._store = store
        self._factory = factory
        self._sink = sink
        self._probe_transport = probe_transport
        self._config = UserConfig()
        self._current: MessagingService | None = None
        self._restart = asyncio.Event()
        self._swapped = asyncio.Event()

    @property
    def current(self) -> MessagingService:
        if self._current is None:
            raise RuntimeError("ServiceManager.start() has not run")
        return self._current

    @property
    def config(self) -> UserConfig:
        return self._config

    async def start(self) -> None:
        self._config = await asyncio.to_thread(self._store.load)
        self._current = self._factory(self._config)

    async def run(self) -> None:
        while True:
            service = self.current
            run_task = asyncio.create_task(service.run())
            restart_task = asyncio.create_task(self._restart.wait())
            done, pending = await asyncio.wait({run_task, restart_task}, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if run_task in done and (error := run_task.exception()) is not None:
                # Supervisor boundary: a crashed backend is logged and rebuilt, not fatal.
                logger.error("messaging backend stopped", exc_info=error)
                with contextlib.suppress(TimeoutError):
                    async with asyncio.timeout(_CRASH_RESTART_DELAY):
                        await self._restart.wait()
            self._restart.clear()
            with contextlib.suppress(OSError):
                await service.close()
            self._current = self._factory(self._config)
            self._swapped.set()
            await self._sink.publish(StatusUpdate(status=await self._current.status()))

    async def connect_mac(self, url: str, password: str) -> AppStatus:
        normalized = normalize_mac_url(url)
        if not password:
            raise RequestError("Enter the BlueBubbles server password.")
        client = BlueBubblesClient(normalized, password, transport=self._probe_transport)
        try:
            info = await client.server_info()
        except BlueBubblesAuthError as exc:
            raise RequestError("The Mac rejected that password.") from exc
        except BlueBubblesError as exc:
            raise RequestError(f"{exc} Check the address and that BlueBubbles Server is running.") from exc
        finally:
            await client.close()
        logger.info("connected to BlueBubbles %s on macOS %s", info.server_version, info.os_version)
        await self._apply(UserConfig(mac=MacConnection(url=normalized, password=SecretStr(password))))
        return await self.current.status()

    async def disconnect_mac(self) -> AppStatus:
        await self._apply(UserConfig())
        return await self.current.status()

    async def _apply(self, config: UserConfig) -> None:
        await asyncio.to_thread(self._store.save, config)
        self._config = config
        self._swapped.clear()
        self._restart.set()
        # Let run() swap the service before answering, so the reply shows the new mode.
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(_SWAP_TIMEOUT):
                await self._swapped.wait()
