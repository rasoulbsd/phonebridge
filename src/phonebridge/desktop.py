"""Opens the UI in a chromeless Edge window so it feels like a desktop app."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import socket
import subprocess
import webbrowser
from pathlib import Path

logger = logging.getLogger(__name__)

_EDGE_RELATIVE = Path("Microsoft") / "Edge" / "Application" / "msedge.exe"
_PORT_PROBE_TIMEOUT = 0.5


def find_edge() -> str | None:
    found = shutil.which("msedge")
    if found:
        return found
    for variable in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(variable)
        if base and (Path(base) / _EDGE_RELATIVE).is_file():
            return str(Path(base) / _EDGE_RELATIVE)
    return None


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(_PORT_PROBE_TIMEOUT)
        return probe.connect_ex(("127.0.0.1", port)) == 0


async def open_window(url: str) -> None:
    edge = find_edge()
    if edge is None:
        await asyncio.to_thread(webbrowser.open, url)
        return
    await asyncio.create_subprocess_exec(
        edge,
        f"--app={url}",
        "--window-size=1180,800",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    logger.info("opened app window")


async def open_in_edge(url: str) -> bool:
    """Opens a link (e.g. FaceTime web, which needs Edge or Chrome) in its own Edge window."""
    edge = find_edge()
    if edge is None:
        return await asyncio.to_thread(webbrowser.open, url)
    await asyncio.create_subprocess_exec(
        edge, f"--app={url}", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    return True
