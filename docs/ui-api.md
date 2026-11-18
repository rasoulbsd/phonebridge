# Phonebridge UI API (v2)

The browser/desktop UI talks only to this API. Two backends implement it:

- **Mac mode**: a Mac running BlueBubbles Server is the bridge. Full iMessage: groups, attachments,
  reactions, replies, edit/unsend, typing, read receipts, FaceTime links.
- **Phone mode**: the iPhone over Bluetooth (MAP). 1:1 texts only, no attachments/reactions/etc.

The UI must adapt to `status.features` (hide what isn't supported) instead of checking `mode`.

Conventions: JSON, ISO-8601 datetimes with offset, ids are **opaque strings** (always
`encodeURIComponent` them in paths). Every non-GET request sends `X-Requested-With: phonebridge`.
401 = token required (show lock screen). Errors: `{"detail": "human sentence"}`.

## Types

```text
Person      = { address: str, name: str | null, photo: str | null }   // photo: URL under /api/photos

Features    = { groups, attachments, send_attachments, reactions, replies, edit, unsend,
                typing, read_receipts, manage_groups, new_group, facetime, direct_calls,
                search, contacts }   // all bool; direct_calls: the Mac's FaceTime helper is running

Status      = { mode: "mac" | "phone" | "demo",
                state: "not_configured" | "connecting" | "connected" | "disconnected"
                       | "busy" | "no_phone" | "auth_failed",
                device_name: str | null,        // "Jane's MacBook Pro" or "Jane's iPhone"
                detail: str | null,             // sentence for the banner
                features: Features,
                notifications: "push" | "polling" | null,
                last_sync_at: str | null,
                contacts_count: int,
                mac: { url: str, server_version: str | null, macos_version: str | null,
                       private_api: bool } | null }

Chat        = { id: str, title: str, is_group: bool,
                service: "iMessage" | "SMS" | "RCS" | null,
                participants: Person[],          // 1:1 -> exactly one (the other person)
                has_custom_name: bool,           // group renamed by someone
                last_text: str, last_at: str | null, last_from_me: bool,
                unread: int }

Attachment  = { id: str, name: str, mime: str, size: int,
                width: int | null, height: int | null,
                url: str,                        // full file (images converted to JPEG)
                thumb_url: str | null,           // small preview for images/videos
                available: bool }                // false: not downloaded on the Mac / not over Bluetooth

Reaction    = { type: "love" | "like" | "dislike" | "laugh" | "emphasize" | "question",
                from_me: bool, sender: Person | null }

Message     = { id: str, chat_id: str, temp_id: str | null,   // temp_id echoes the optimistic send
                from_me: bool, sender: Person | null,         // null when from_me
                text: str, at: str,
                status: "sending" | "sent" | "delivered" | "read" | "failed" | "received",
                delivered_at: str | null, read_at: str | null, error: str | null,
                attachments: Attachment[], reactions: Reaction[],
                reply_to: { id: str, text: str, sender_name: str | null } | null,
                edited: bool, unsent: bool,
                event: str | null }   // group system line ("Sam named the conversation “Trip”"); render centered, no bubble

ThreadPage  = { chat: Chat, messages: Message[] /* oldest first */, cursor: str | null /* pass as ?before= for older; null = no more */ }
SearchHit   = { message: Message, chat: Chat }
Contact     = { name: str, addresses: str[] }
Call        = { id: str,
                status: "incoming" | "ringing"                 // a call to the Mac
                      | "outgoing" | "answered" | "ready"      // a call this PC placed, in order
                      | "connected" | "ended",
                caller: Person | null,                         // for outgoing calls: who is being called
                is_video: bool, outgoing: bool,
                detail: str | null }                           // why an outgoing call ended
```

## Endpoints

| Method | Path | Body / query | Returns |
| --- | --- | --- | --- |
| GET | `/api/status` | | Status |
| GET | `/api/chats` | `?q=` filters by title/participants | Chat[] newest first |
| GET | `/api/chats/{id}` | | Chat |
| GET | `/api/chats/{id}/messages` | `?limit=50&before=<cursor>` | ThreadPage |
| POST | `/api/chats/{id}/messages` | `{text, reply_to?: messageId, temp_id}` | 202 Message (`status: "sending"`) |
| POST | `/api/chats/{id}/attachments` | multipart: `file`, `temp_id` | 202 Message |
| POST | `/api/chats` | `{addresses: str[], text}` (1 address = DM, more = new group) | 201 ThreadPage |
| POST | `/api/chats/{id}/read` | | 204 |
| POST | `/api/chats/{id}/typing` | `{typing: bool}` | 204 |
| PATCH | `/api/chats/{id}` | `{title}` (rename group) | Chat |
| POST | `/api/chats/{id}/participants` | `{address}` | Chat |
| DELETE | `/api/chats/{id}/participants/{address}` | | Chat |
| POST | `/api/chats/{id}/leave` | | 204 |
| POST | `/api/messages/{id}/reactions` | `{chat_id, type, remove?: bool}` | 202 |
| POST | `/api/messages/{id}/edit` | `{chat_id, text}` | 202 |
| POST | `/api/messages/{id}/unsend` | `{chat_id}` | 202 |
| POST | `/api/messages/{id}/retry` | `{chat_id}` | 202 Message |
| GET | `/api/attachments/{id}` | `?thumb=1` | file bytes (proper Content-Type) |
| GET | `/api/search` | `?q=&limit=50` | SearchHit[] |
| GET | `/api/contacts` | `?q=&limit=20` | Contact[] |
| POST | `/api/calls/{id}/answer` | | `{link: str, opened: bool}` |
| POST | `/api/calls/{id}/decline` | | 204 |
| POST | `/api/calls` | `{chat_id, video?: bool}` ring the other person from the Mac (1:1 chats, needs `direct_calls`) | `Call` |
| POST | `/api/calls/{id}/join` | once an outgoing call is `ready`: open it on this PC | `{link: str, opened: bool}` |
| GET | `/api/photos/{id}` | contact photo (cache forever: ids are content hashes) | image |
| GET | `/api/settings` | | `{mode, mac: {url} \| null}` |
| PUT | `/api/settings/mac` | `{url, password}` (tests the connection first; 422 with detail on failure) | Status |
| DELETE | `/api/settings/mac` | back to phone mode | Status |
| POST | `/api/phone-link/close` | phone mode only | 204 |

`opened: true` means the backend already opened the FaceTime link in Edge on this PC (desktop app);
otherwise the UI should `window.open(link)` (FaceTime web needs Edge or Chrome).

## WebSocket `/ws`

Server -> UI (JSON):

- `{"type": "status", "status": Status}`
- `{"type": "chat", "chat": Chat}` (new or changed chat, e.g. rename, participants, unread)
- `{"type": "chat_removed", "chat_id": str}` (left a group)
- `{"type": "message", "message": Message, "chat": Chat}` (new or updated message, matched by `id`, or by `temp_id` for optimistic sends; reactions/edits/unsend/read arrive as updates)
- `{"type": "typing", "chat_id": str, "typing": bool, "person": Person | null}`
- `{"type": "call", "call": Call}`

UI -> server: `{"type": "focus", "focused": bool, "chat_id": str | null}` whenever the window gains or
loses focus or visibility, or the open conversation changes. `chat_id` is the conversation visible in
front of the user (null for the list or empty view). The desktop app holds back native notifications
only for that conversation; messages in other conversations still pop up.

## Desktop app integration

When running inside the desktop app, `window.pywebview` exists. Then the UI must not use web
`Notification`s (the app shows native ones) and must expose:

```js
window.phonebridge = { openConversation(chatId) }   // called when a notification is clicked
```
