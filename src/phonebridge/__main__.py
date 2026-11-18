"""Command-line entry point: runs the backend and opens the UI in an Edge app window."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys
import webbrowser

from phonebridge.desktop import open_window, port_in_use
from phonebridge.runtime import Runtime, configure_logging
from phonebridge.settings import Settings

logger = logging.getLogger("phonebridge")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="phonebridge", description="Your iPhone's texts on this PC.")
    parser.add_argument("--demo", action="store_true", help="use a pretend phone with fictional messages")
    parser.add_argument("--no-window", action="store_true", help="don't open the app window")
    parser.add_argument("--host", help="interface to listen on (default 127.0.0.1)")
    parser.add_argument("--port", type=int, help="port to listen on (default 8765)")
    return parser.parse_args(argv)


def settings_from_args(args: argparse.Namespace) -> Settings:
    overrides: dict[str, object] = {}
    if args.demo:
        overrides["demo"] = True
    if getattr(args, "no_window", False):
        overrides["open_window"] = False
    if args.host:
        overrides["host"] = args.host
    if args.port:
        overrides["port"] = args.port
    return Settings.model_validate(overrides)


async def _run(runtime: Runtime) -> None:
    async def open_when_ready() -> None:
        await asyncio.to_thread(runtime.ready.wait)
        await open_window(runtime.settings.url)

    async with asyncio.TaskGroup() as tg:
        window_task = tg.create_task(open_when_ready()) if runtime.settings.open_window else None
        await runtime.serve()
        if window_task is not None:
            window_task.cancel()


def main(argv: list[str] | None = None) -> None:
    settings = settings_from_args(parse_args(argv))
    if not settings.is_loopback and settings.token is None:
        sys.exit("Set PHONEBRIDGE_TOKEN before listening on a non-local address.")
    if port_in_use(settings.port):
        # Already running: just bring up a window for the existing instance.
        if settings.open_window:
            webbrowser.open(settings.url)
        return
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    configure_logging(settings)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run(Runtime(settings)))


if __name__ == "__main__":
    main()
