# Changelog

## 0.2.11

- Right-to-left languages (Persian, Arabic, Hebrew): each line of a message, the message box and the
  new-message box takes the direction of its first letter, so Persian reads and types right to left
  while English stays left to right, even when both are mixed in one message. Search, group names,
  chat previews and reply quotes follow too.

## 0.2.10

- Download works in the desktop app: it opens the Windows Save As dialog (in Downloads) for photos and
  files, from the message menu, file chips and the photo viewer. Downloads were silently blocked before.
- New **Copy image** / **Copy file** in the message menu and the photo viewer. Copied photos paste as a
  picture into chats and documents and as a file into Explorer; other files paste into folders and emails.

## 0.2.9

- The settings backup in the registry is also written when the app starts with a good settings file, so
  installs from before 0.2.8 get one too.
- `packaging/install-local.cmd` installs the newest personal build silently; open it from Explorer.

## 0.2.8

- Messages are kept on this PC: chats, conversations, contacts, contact photos and the photos and files you
  open are saved as they arrive from the Mac, and each time the Mac connects the newest messages of every
  chat are fetched. While the Mac is asleep, off or unreachable, everything stays readable and searchable
  instead of disappearing.
- The Mac connection survives losing the data folder: a DPAPI-encrypted copy of the settings is kept in the
  user registry and restored automatically.

## 0.2.7

- A message that fails to send now says why in plain words: the person doesn't use iMessage, or a
  text message couldn't go out because the iPhone isn't relaying texts for the Mac (Text Message
  Forwarding).

## 0.2.6

First public release.

- Notifications pop up again: the installer now stamps the app's ID on its Start menu shortcut, which
  Windows requires before it shows banners for an installed desktop app.
- New messages are sent as important notifications, so they can break through Do Not Disturb; incoming
  FaceTime calls ring as call notifications and clear when the call ends.
- Notifications are held back only for the conversation you're reading; other chats still pop up.
- Public builds use Phonebridge's own name and icon; a gitignored file can rebrand a personal build.

## 0.2.x (before the public repo)

- Mac mode through BlueBubbles Server: full iMessage history, group chats, photos and files, reactions,
  replies, edit and unsend, typing indicators, read receipts, group management, contact photos.
- FaceTime: answer and place calls from the PC through FaceTime web links in Edge.
- Installable desktop app: WebView2 window, tray icon, start with Windows, native notifications,
  per-user installer and uninstaller.
- iPhone over Bluetooth (MAP/PBAP) as a fallback for 1:1 texts.
