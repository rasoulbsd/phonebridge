# Changelog

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
