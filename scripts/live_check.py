"""Privacy-safe connection check against the real phone.

Prints counts and flags only: no message text, names or numbers. Sends nothing.
Usage: .venv\\Scripts\\python.exe scripts\\live_check.py [BT address]
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections import Counter
from datetime import UTC, datetime

from phonebridge.bluetooth import WinRtPhoneConnector
from phonebridge.services import PhoneError


async def main(address: str | None) -> None:
    local_tz = datetime.now(UTC).astimezone().tzinfo or UTC
    connector = WinRtPhoneConnector(address=address, local_tz=local_tz)
    try:
        session = await connector.connect()
    except PhoneError as exc:
        print(f"connect failed: {type(exc).__name__}: {exc}")
        await connector.close()
        return
    print(f"connected to: {session.phone_name}")
    try:
        inbox = await session.list_inbox()
        print(f"inbox messages: {len(inbox)}")
        print(f"  full text in listing: {sum(m.has_full_text for m in inbox)} / {len(inbox)}")
        print(f"  unread: {sum(not m.read for m in inbox)}, with attachments: {sum(m.attachment_size > 0 for m in inbox)}")
        print(f"  have conversation_id: {sum(bool(m.conversation_id) for m in inbox)}")
        print(f"  have conversation_name: {sum(bool(m.conversation_name) for m in inbox)}")
        print(f"  have direction: {sum(bool(m.direction) for m in inbox)}")
        print(f"  timestamps with offset: {sum(m.sent_at.utcoffset() is not None for m in inbox)}")
        print(f"  subject length vs size: {Counter((len(m.subject.encode()) >= m.size) for m in inbox)}")
        if inbox:
            dates = sorted(m.sent_at for m in inbox)
            print(f"  date range: {dates[0]:%Y-%m-%d} .. {dates[-1]:%Y-%m-%d}")
        try:
            contacts = await session.pull_contacts()
            print(f"contacts: {len(contacts)}, with several addresses: {sum(len(c.addresses) > 1 for c in contacts)}")
        except PhoneError as exc:
            print(f"contacts failed: {exc}")
        for second in range(8):
            if session.push_enabled:
                print(f"push notifications: active after ~{second}s")
                break
            await asyncio.sleep(1)
        else:
            print("push notifications: not active (app will poll)")
    finally:
        await session.close()
        await connector.close()
    print("closed")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None))
