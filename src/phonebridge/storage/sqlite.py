"""Message history and contacts in a local SQLite file."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Self

import aiosqlite

from phonebridge.domain import (
    Contact,
    Conversation,
    Direction,
    Message,
    MessageStatus,
    SearchHit,
    conversation_key,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from pathlib import Path

    from phonebridge.domain import NewMessage

_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    address TEXT NOT NULL,
    name TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key TEXT NOT NULL UNIQUE,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    direction TEXT NOT NULL,
    address TEXT NOT NULL,
    text TEXT NOT NULL,
    at TEXT NOT NULL,
    read INTEGER NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    has_attachment INTEGER NOT NULL DEFAULT 0,
    phone_handle TEXT,
    session_id TEXT
);
CREATE INDEX IF NOT EXISTS messages_by_conversation ON messages(conversation_id, at, id);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    text, content='messages', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE OF text ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contact_addresses (
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    address TEXT NOT NULL,
    match_key TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS contact_addresses_by_key ON contact_addresses(match_key);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_CONVERSATIONS = """
SELECT * FROM (
    SELECT
        c.id AS id,
        c.address AS address,
        COALESCE(
            (SELECT ct.name FROM contact_addresses ca JOIN contacts ct ON ct.id = ca.contact_id
             WHERE ca.match_key = c.id ORDER BY ct.id LIMIT 1),
            c.name
        ) AS name,
        m.text AS last_text,
        m.at AS last_at,
        m.id AS last_id,
        m.direction AS last_direction,
        (SELECT COUNT(*) FROM messages u
         WHERE u.conversation_id = c.id AND u.read = 0 AND u.direction = 'in') AS unread
    FROM conversations c
    JOIN messages m ON m.id = (
        SELECT id FROM messages WHERE conversation_id = c.id ORDER BY at DESC, id DESC LIMIT 1
    )
)
"""

_TOKENS = re.compile(r"\w+", re.UNICODE)


def _like(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _fts_query(query: str) -> str:
    return " ".join(f'"{token}"*' for token in _TOKENS.findall(query))


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _message(row: aiosqlite.Row) -> Message:
    return Message(
        id=row["id"],
        conversation_id=row["conversation_id"],
        direction=Direction(row["direction"]),
        address=row["address"],
        text=row["text"],
        at=datetime.fromisoformat(row["at"]),
        read=bool(row["read"]),
        status=MessageStatus(row["status"]),
        error=row["error"],
        has_attachment=bool(row["has_attachment"]),
        phone_handle=row["phone_handle"],
        session_id=row["session_id"],
    )


def _conversation(row: aiosqlite.Row) -> Conversation:
    return Conversation(
        id=row["id"],
        address=row["address"],
        name=row["name"],
        last_text=row["last_text"],
        last_at=datetime.fromisoformat(row["last_at"]),
        last_direction=Direction(row["last_direction"]),
        unread=row["unread"],
    )


class SqliteRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._db = connection
        # One connection serves both the sync loop and web requests; a commit while
        # another call still has a statement open fails, so calls run one at a time.
        self._lock = asyncio.Lock()

    @classmethod
    async def open(cls, path: Path | str) -> Self:
        connection = await aiosqlite.connect(path)
        connection.row_factory = aiosqlite.Row
        await connection.executescript(_SCHEMA)
        await connection.commit()
        return cls(connection)

    async def close(self) -> None:
        async with self._lock:
            await self._db.close()

    # Messages

    async def add_message(self, message: NewMessage) -> Message | None:
        async with self._lock:
            await self._db.execute(
                "INSERT INTO conversations (id, address, name) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name = COALESCE(excluded.name, conversations.name)",
                (message.conversation_id, message.address, message.name),
            )
            cursor = await self._db.execute(
                "INSERT OR IGNORE INTO messages (dedupe_key, conversation_id, direction, address, text, at, read, "
                "status, error, has_attachment, phone_handle, session_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    message.dedupe_key,
                    message.conversation_id,
                    message.direction.value,
                    message.address,
                    message.text,
                    _iso(message.at),
                    int(message.read),
                    message.status.value,
                    message.error,
                    int(message.has_attachment),
                    message.phone_handle,
                    message.session_id,
                ),
            )
            await self._db.commit()
            if cursor.rowcount == 0 or cursor.lastrowid is None:
                return None
            return await self._fetch_message(cursor.lastrowid)

    async def get_message(self, message_id: int) -> Message | None:
        async with self._lock:
            return await self._fetch_message(message_id)

    async def _fetch_message(self, message_id: int) -> Message | None:
        async with self._db.execute("SELECT * FROM messages WHERE id = ?", (message_id,)) as cursor:
            row = await cursor.fetchone()
        return _message(row) if row else None

    async def update_message_status(
        self,
        message_id: int,
        status: MessageStatus,
        *,
        error: str | None = None,
        phone_handle: str | None = None,
        session_id: str | None = None,
    ) -> Message:
        async with self._lock:
            await self._db.execute(
                "UPDATE messages SET status = ?, error = ?, "
                "phone_handle = COALESCE(?, phone_handle), session_id = COALESCE(?, session_id) WHERE id = ?",
                (status.value, error, phone_handle, session_id, message_id),
            )
            await self._db.commit()
            message = await self._fetch_message(message_id)
            if message is None:
                raise LookupError(f"message {message_id} does not exist")
            return message

    async def known_keys(self, keys: Iterable[str]) -> set[str]:
        async with (
            self._lock,
            self._db.execute(
                "SELECT dedupe_key FROM messages WHERE dedupe_key IN (SELECT value FROM json_each(?))",
                (json.dumps(list(keys)),),
            ) as cursor,
        ):
            return {row[0] for row in await cursor.fetchall()}

    async def attach_handles(self, handles: Mapping[str, str], session_id: str) -> None:
        async with self._lock:
            await self._db.executemany(
                "UPDATE messages SET phone_handle = ?, session_id = ? WHERE dedupe_key = ?",
                [(handle, session_id, key) for key, handle in handles.items()],
            )
            await self._db.commit()

    async def mark_read_by_keys(self, keys: Iterable[str]) -> list[str]:
        async with self._lock:
            async with self._db.execute(
                "UPDATE messages SET read = 1 WHERE read = 0 AND direction = 'in' "
                "AND dedupe_key IN (SELECT value FROM json_each(?)) RETURNING conversation_id",
                (json.dumps(list(keys)),),
            ) as cursor:
                changed = {row[0] for row in await cursor.fetchall()}
            await self._db.commit()
            return sorted(changed)

    async def mark_conversation_read(self, conversation_id: str) -> list[Message]:
        async with self._lock:
            async with self._db.execute(
                "UPDATE messages SET read = 1 WHERE conversation_id = ? AND read = 0 AND direction = 'in' "
                "RETURNING *",
                (conversation_id,),
            ) as cursor:
                rows = list(await cursor.fetchall())
            await self._db.commit()
            return [_message(row) for row in rows]

    async def list_messages(
        self, conversation_id: str, *, before_id: int | None, limit: int
    ) -> tuple[list[Message], bool]:
        async with self._lock:
            if before_id is None:
                sql = "SELECT * FROM messages WHERE conversation_id = ? ORDER BY at DESC, id DESC LIMIT ?"
                params: tuple[object, ...] = (conversation_id, limit + 1)
            else:
                sql = (
                    "SELECT * FROM messages WHERE conversation_id = ? "
                    "AND (at, id) < (SELECT at, id FROM messages WHERE id = ?) "
                    "ORDER BY at DESC, id DESC LIMIT ?"
                )
                params = (conversation_id, before_id, limit + 1)
            async with self._db.execute(sql, params) as cursor:
                rows = list(await cursor.fetchall())
            has_more = len(rows) > limit
            return [_message(row) for row in reversed(rows[:limit])], has_more

    # Conversations

    async def get_conversation(self, conversation_id: str) -> Conversation | None:
        async with self._lock:
            return await self._fetch_conversation(conversation_id)

    async def _fetch_conversation(self, conversation_id: str) -> Conversation | None:
        async with self._db.execute(_CONVERSATIONS + " WHERE id = ?", (conversation_id,)) as cursor:
            row = await cursor.fetchone()
        return _conversation(row) if row else None

    async def list_conversations(self, query: str | None = None) -> list[Conversation]:
        async with self._lock:
            sql = _CONVERSATIONS
            params: tuple[object, ...] = ()
            if query and query.strip():
                sql += " WHERE name LIKE ? ESCAPE '\\' OR address LIKE ? ESCAPE '\\'"
                params = (_like(query.strip()), _like(query.strip()))
            sql += " ORDER BY last_at DESC, last_id DESC"
            async with self._db.execute(sql, params) as cursor:
                rows = list(await cursor.fetchall())
            return [_conversation(row) for row in rows]

    async def search(self, query: str, limit: int) -> list[SearchHit]:
        async with self._lock:
            match = _fts_query(query)
            if not match:
                return []
            async with self._db.execute(
                "SELECT m.* FROM messages_fts f JOIN messages m ON m.id = f.rowid "
                "WHERE messages_fts MATCH ? ORDER BY m.at DESC, m.id DESC LIMIT ?",
                (match, limit),
            ) as cursor:
                rows = list(await cursor.fetchall())
            hits: list[SearchHit] = []
            conversations: dict[str, Conversation | None] = {}
            for row in rows:
                message = _message(row)
                if message.conversation_id not in conversations:
                    conversations[message.conversation_id] = await self._fetch_conversation(
                        message.conversation_id
                    )
                conversation = conversations[message.conversation_id]
                if conversation is not None:
                    hits.append(SearchHit(message=message, conversation=conversation))
            return hits

    # Contacts

    async def replace_contacts(self, contacts: Sequence[Contact]) -> None:
        async with self._lock:
            await self._db.execute("DELETE FROM contact_addresses")
            await self._db.execute("DELETE FROM contacts")
            await self._db.executemany(
                "INSERT INTO contacts (id, name) VALUES (?, ?)",
                [(contact_id, contact.name) for contact_id, contact in enumerate(contacts, start=1)],
            )
            await self._db.executemany(
                "INSERT INTO contact_addresses (contact_id, address, match_key) VALUES (?, ?, ?)",
                [
                    (contact_id, address, conversation_key(address))
                    for contact_id, contact in enumerate(contacts, start=1)
                    for address in contact.addresses
                ],
            )
            await self._db.commit()

    async def search_contacts(self, query: str, limit: int) -> list[Contact]:
        async with self._lock:
            pattern = _like(query.strip())
            async with self._db.execute(
                "SELECT id, name FROM contacts c WHERE c.name LIKE ? ESCAPE '\\' OR EXISTS ("
                "SELECT 1 FROM contact_addresses a WHERE a.contact_id = c.id AND a.address LIKE ? ESCAPE '\\') "
                "ORDER BY c.name COLLATE NOCASE LIMIT ?",
                (pattern, pattern, limit),
            ) as cursor:
                rows = list(await cursor.fetchall())
            contacts: list[Contact] = []
            for row in rows:
                async with self._db.execute(
                    "SELECT address FROM contact_addresses WHERE contact_id = ? ORDER BY rowid", (row["id"],)
                ) as cursor:
                    addresses = [a[0] for a in await cursor.fetchall()]
                contacts.append(Contact(name=row["name"], addresses=addresses))
            return contacts

    async def contacts_count(self) -> int:
        async with self._lock:
            async with self._db.execute("SELECT COUNT(*) FROM contacts") as cursor:
                row = await cursor.fetchone()
            return int(row[0]) if row else 0

    # Meta

    async def get_meta(self, key: str) -> str | None:
        async with self._lock:
            async with self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)) as cursor:
                row = await cursor.fetchone()
            return str(row[0]) if row else None

    async def set_meta(self, key: str, value: str) -> None:
        async with self._lock:
            await self._db.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            await self._db.commit()
