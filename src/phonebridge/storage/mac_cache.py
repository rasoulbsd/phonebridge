"""Local copy of what the Mac shares (chats, messages, contacts, photos, opened files).

It lets the app show and search conversations while the Mac is asleep, off or unreachable,
and keeps them across restarts. Rows hold the UI models as JSON, so offline reads return
exactly what the live Mac would have.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Self

import aiosqlite

from phonebridge.domain.ui import AppStatus, Chat, ChatMessage, ChatSearchHit, ContactCard
from phonebridge.services.messaging import Photo

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

_SCHEMA = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS chats (
    id TEXT PRIMARY KEY,
    last_at TEXT,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL,
    at_ms INTEGER NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_by_chat ON messages(chat_id, at_ms);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    text, content='messages', content_rowid='rowid', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE OF text ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
    INSERT INTO messages_fts(rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TABLE IF NOT EXISTS contacts (
    position INTEGER PRIMARY KEY,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS photos (
    id TEXT PRIMARY KEY,
    content_type TEXT NOT NULL,
    data BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
    key TEXT PRIMARY KEY,
    content_type TEXT NOT NULL,
    size INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_TOKENS = re.compile(r"\w+", re.UNICODE)
_DEVICE = "device"
_LAST_STATUS = "last_status"
_CLEAR = (
    "DELETE FROM messages",
    "DELETE FROM chats",
    "DELETE FROM contacts",
    "DELETE FROM photos",
    "DELETE FROM files",
)


def at_ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _fts_query(query: str) -> str:
    return " ".join(f'"{token}"*' for token in _TOKENS.findall(query))


def file_key(attachment_id: str, *, thumb: bool) -> str:
    return hashlib.sha256(f"{attachment_id}|{int(thumb)}".encode()).hexdigest()


class MacCache:
    def __init__(self, connection: aiosqlite.Connection, files_dir: Path) -> None:
        self._db = connection
        self.files_dir = files_dir
        # One connection serves the sync loop and web requests; calls run one at a time.
        self._lock = asyncio.Lock()

    @classmethod
    async def open(cls, path: Path | str, files_dir: Path) -> Self:
        connection = await aiosqlite.connect(path)
        connection.row_factory = aiosqlite.Row
        await connection.executescript(_SCHEMA)
        await connection.commit()
        await asyncio.to_thread(files_dir.mkdir, parents=True, exist_ok=True)
        return cls(connection, files_dir)

    async def close(self) -> None:
        await self._db.close()

    async def use_device(self, device: str) -> bool:
        """Clears everything when a different Mac is connected. True if it was cleared."""
        async with self._lock:
            async with self._db.execute("SELECT value FROM meta WHERE key = ?", (_DEVICE,)) as cursor:
                row = await cursor.fetchone()
            previous = str(row[0]) if row else None
            cleared = previous is not None and previous != device
            if cleared:
                for statement in _CLEAR:
                    await self._db.execute(statement)
            await self._db.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (_DEVICE, device),
            )
            await self._db.commit()
            return cleared

    async def save_last_status(self, status: AppStatus) -> None:
        async with self._lock:
            await self._db.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (_LAST_STATUS, status.model_dump_json()),
            )
            await self._db.commit()

    async def last_status(self) -> AppStatus | None:
        """What the Mac looked like the last time it was connected (features, name, versions)."""
        async with (
            self._lock,
            self._db.execute("SELECT value FROM meta WHERE key = ?", (_LAST_STATUS,)) as cursor,
        ):
            row = await cursor.fetchone()
        return AppStatus.model_validate_json(row[0]) if row else None

    # Chats

    async def upsert_chats(self, chats: Sequence[Chat]) -> None:
        if not chats:
            return
        async with self._lock:
            await self._db.executemany(
                "INSERT INTO chats (id, last_at, body) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET last_at = excluded.last_at, body = excluded.body",
                [
                    (c.id, c.last_at.astimezone(UTC).isoformat() if c.last_at else None, c.model_dump_json())
                    for c in chats
                ],
            )
            await self._db.commit()

    async def delete_chat(self, chat_id: str) -> None:
        async with self._lock:
            await self._db.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
            await self._db.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
            await self._db.commit()

    async def chat(self, chat_id: str) -> Chat | None:
        async with self._lock, self._db.execute("SELECT body FROM chats WHERE id = ?", (chat_id,)) as cursor:
            row = await cursor.fetchone()
        return Chat.model_validate_json(row[0]) if row else None

    async def chats(self, query: str | None = None) -> list[Chat]:
        async with (
            self._lock,
            self._db.execute("SELECT body FROM chats ORDER BY last_at IS NULL, last_at DESC") as cursor,
        ):
            rows = list(await cursor.fetchall())
        chats = [Chat.model_validate_json(row[0]) for row in rows]
        if query and query.strip():
            needle = query.strip().casefold()
            chats = [
                c
                for c in chats
                if needle in c.title.casefold()
                or any(
                    needle in p.address.casefold() or needle in (p.name or "").casefold()
                    for p in c.participants
                )
            ]
        return chats

    # Messages

    async def upsert_messages(self, messages: Sequence[ChatMessage]) -> None:
        # Optimistic sends ("temp:...") are replaced by the real message once the Mac confirms.
        rows = [
            (m.id, m.chat_id, at_ms(m.at), m.text, m.model_dump_json())
            for m in messages
            if not m.id.startswith("temp:")
        ]
        if not rows:
            return
        async with self._lock:
            await self._db.executemany(
                "INSERT INTO messages (id, chat_id, at_ms, text, body) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET chat_id = excluded.chat_id, at_ms = excluded.at_ms, "
                "text = excluded.text, body = excluded.body",
                rows,
            )
            await self._db.commit()

    async def page(
        self, chat_id: str, *, before_ms: int | None, limit: int
    ) -> tuple[list[ChatMessage], str | None]:
        """Newest `limit` messages older than `before_ms`, oldest first, plus the cursor for older ones."""
        if before_ms is None:
            sql = "SELECT body, at_ms FROM messages WHERE chat_id = ? ORDER BY at_ms DESC LIMIT ?"
            params: tuple[object, ...] = (chat_id, limit + 1)
        else:
            sql = (
                "SELECT body, at_ms FROM messages WHERE chat_id = ? AND at_ms < ? ORDER BY at_ms DESC LIMIT ?"
            )
            params = (chat_id, before_ms, limit + 1)
        async with self._lock, self._db.execute(sql, params) as cursor:
            rows = list(await cursor.fetchall())
        kept = rows[:limit]
        messages = [ChatMessage.model_validate_json(row[0]) for row in reversed(kept)]
        cursor_value = str(kept[-1][1]) if len(rows) > limit and kept else None
        return messages, cursor_value

    async def latest_ms(self, chat_id: str) -> int | None:
        async with (
            self._lock,
            self._db.execute("SELECT MAX(at_ms) FROM messages WHERE chat_id = ?", (chat_id,)) as cursor,
        ):
            row = await cursor.fetchone()
        return int(row[0]) if row and row[0] is not None else None

    async def message_count(self) -> int:
        async with self._lock, self._db.execute("SELECT COUNT(*) FROM messages") as cursor:
            row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def search(self, query: str, limit: int) -> list[ChatSearchHit]:
        match = _fts_query(query)
        if not match:
            return []
        async with self._lock:
            async with self._db.execute(
                "SELECT m.body FROM messages_fts f JOIN messages m ON m.rowid = f.rowid "
                "WHERE messages_fts MATCH ? ORDER BY m.at_ms DESC LIMIT ?",
                (match, limit),
            ) as cursor:
                rows = list(await cursor.fetchall())
            messages = [ChatMessage.model_validate_json(row[0]) for row in rows]
            chats: dict[str, Chat] = {}
            for chat_id in {m.chat_id for m in messages}:
                async with self._db.execute("SELECT body FROM chats WHERE id = ?", (chat_id,)) as cursor:
                    row = await cursor.fetchone()
                if row:
                    chats[chat_id] = Chat.model_validate_json(row[0])
        return [ChatSearchHit(message=m, chat=chats[m.chat_id]) for m in messages if m.chat_id in chats]

    # Contacts and photos

    async def save_contacts(self, cards: Sequence[ContactCard]) -> None:
        async with self._lock:
            await self._db.execute("DELETE FROM contacts")
            await self._db.executemany(
                "INSERT INTO contacts (position, body) VALUES (?, ?)",
                [(i, card.model_dump_json()) for i, card in enumerate(cards)],
            )
            await self._db.commit()

    async def contacts(self, query: str, limit: int) -> list[ContactCard]:
        async with self._lock, self._db.execute("SELECT body FROM contacts ORDER BY position") as cursor:
            rows = list(await cursor.fetchall())
        needle = query.strip().casefold()
        cards = [ContactCard.model_validate_json(row[0]) for row in rows]
        return [
            c
            for c in cards
            if not needle or needle in c.name.casefold() or any(needle in a.casefold() for a in c.addresses)
        ][:limit]

    async def save_photo(self, photo_id: str, photo: Photo) -> None:
        async with self._lock:
            await self._db.execute(
                "INSERT INTO photos (id, content_type, data) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET content_type = excluded.content_type, data = excluded.data",
                (photo_id, photo.content_type, photo.data),
            )
            await self._db.commit()

    async def photo(self, photo_id: str) -> Photo | None:
        async with (
            self._lock,
            self._db.execute("SELECT content_type, data FROM photos WHERE id = ?", (photo_id,)) as cursor,
        ):
            row = await cursor.fetchone()
        return Photo(content_type=str(row[0]), data=bytes(row[1])) if row else None

    # Opened attachments (files on disk)

    async def file(self, attachment_id: str, *, thumb: bool) -> tuple[Path, str, int] | None:
        key = file_key(attachment_id, thumb=thumb)
        async with (
            self._lock,
            self._db.execute("SELECT content_type, size FROM files WHERE key = ?", (key,)) as cursor,
        ):
            row = await cursor.fetchone()
        path = self.files_dir / key
        if row is None or not await asyncio.to_thread(path.is_file):
            return None
        return path, str(row[0]), int(row[1])

    async def remember_file(self, attachment_id: str, *, thumb: bool, content_type: str, size: int) -> None:
        async with self._lock:
            await self._db.execute(
                "INSERT INTO files (key, content_type, size) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET content_type = excluded.content_type, size = excluded.size",
                (file_key(attachment_id, thumb=thumb), content_type, size),
            )
            await self._db.commit()
