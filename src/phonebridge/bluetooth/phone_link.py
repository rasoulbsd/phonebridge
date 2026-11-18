from __future__ import annotations

import asyncio
import logging
import subprocess

logger = logging.getLogger(__name__)

_PROCESS = "PhoneExperienceHost.exe"
_TASKKILL_NOT_FOUND = 128
# Give Windows a moment to release the RFCOMM channel Phone Link held.
_RELEASE_DELAY = 1.5


class WindowsPhoneLink:
    async def close_phone_link(self) -> bool:
        process = await asyncio.create_subprocess_exec(
            "taskkill.exe",
            "/IM",
            _PROCESS,
            "/F",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        code = await process.wait()
        if code == 0:
            logger.info("closed Phone Link")
            await asyncio.sleep(_RELEASE_DELAY)
        return code in (0, _TASKKILL_NOT_FOUND)
