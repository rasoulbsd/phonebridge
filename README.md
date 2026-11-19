# Phonebridge

Your iPhone's messages on your Windows PC. Two ways to connect:

| | Mac at home (recommended) | iPhone over Bluetooth |
| --- | --- | --- |
| How | A Mac signed in to your Apple ID runs BlueBubbles Server; Phonebridge talks to it over your network | Phonebridge talks to the iPhone directly, like Phone Link |
| Messages | Full iMessage + SMS history, group chats, photos and files, sent messages | Only new 1:1 texts received while connected |
| Extras (need the Private API, SIP off on the Mac) | Reactions, replies, edit/unsend, typing indicators, create/rename/manage groups, answer FaceTime in Edge | none |
| Needs | Mac stays on, awake and logged in | iPhone within Bluetooth range |

> Phonebridge is an independent project, not affiliated with or endorsed by Apple. iMessage, FaceTime,
> iPhone and Mac are trademarks of Apple Inc. In Mac mode it talks to [BlueBubbles Server](https://bluebubbles.app),
> a separate open-source project.

## Install on Windows

Download `Phonebridge-Setup-<version>.exe` from the [latest release](../../releases/latest) and run it. It installs for your account (no admin rights), adds Phonebridge
to the Start menu and desktop, starts it with Windows (toggle in the tray icon menu), and can be removed
from **Settings > Apps**. Closing the window keeps it running in the tray; notifications appear when the
window isn't focused.

## Connect your Mac

On the Mac (one time):

1. **System Settings > General > Sharing**: turn on **Remote Login** if you want the setup done for you,
   or just run the script yourself: `bash packaging/mac/setup-bluebubbles.sh`. It installs BlueBubbles
   Server, keeps it on your local network only (port 1234), starts it at login, keeps the Mac awake, and
   writes a random server password to **Phonebridge Mac password.txt** on the Desktop.
2. **System Settings > Privacy & Security > Full Disk Access**: turn on **BlueBubbles**. Allow the
   Contacts prompt, and the "control Messages" prompt the first time you send.
3. Keep it awake with the lid closed (optional): `sudo pmset -c disablesleep 1`.

In Phonebridge: **Settings > Mac**, enter `http://<Mac's IP>:1234` and the password from that file.

### Full features (Private API)

Reactions, replies, editing, typing indicators, group management and FaceTime answering need the
BlueBubbles Private API, which requires turning off System Integrity Protection on the Mac. This lowers
the Mac's security (iPhone/iPad apps stop running on Apple silicon Macs, for example). Steps:

1. Terminal: `sudo defaults write /Library/Preferences/com.apple.security.libraryvalidation.plist DisableLibraryValidation -bool true`
2. Shut down, hold the power button until "Loading startup options", choose **Options**, then
   **Utilities > Terminal** and run `csrutil disable`. Restart.
3. Run `ENABLE_PRIVATE_API=1 bash packaging/mac/setup-bluebubbles.sh`.

On macOS 26 (Tahoe) the current BlueBubbles helper is known to be unreliable; macOS 15 works best.

### FaceTime

When someone FaceTimes you, Phonebridge shows the call. **Answer** makes the Mac pick up and hand the call
to a FaceTime web link that opens in Edge on your PC (allow camera and microphone, then Join). Calling
someone sends them a FaceTime link in the chat. Group FaceTime, SharePlay and screen sharing aren't
available in the browser.

## Run from source

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
python -m phonebridge.shell        # desktop app (window + tray)
python -m phonebridge              # server + Edge window, no tray
python -m phonebridge.shell --demo # fictional data, no phone or Mac needed
```

Build the installer: `python packaging/build.py` (writes `dist/Phonebridge-Setup-<version>.exe`).

Personal branding: a gitignored `src/phonebridge/branding_local.py` can set `DISPLAY_NAME`, `ICON_PNG` and
`ICON_ICO` for your own copy; build it with `python packaging/build.py --personal`. Install it with
`packaging\install-local.cmd` (double-click it in Explorer). Public builds and
releases never include it (the build checks).

Release a version: bump `__version__` in `src/phonebridge/__init__.py`, add its section to
`CHANGELOG.md`, commit, then run `bash packaging/release.sh` from WSL. It runs the checks, builds the public installer,
tags `v<version>`, pushes, and publishes a GitHub release with the installer attached.

## Settings

Environment variables or a `.env` file next to `pyproject.toml`:

| Variable | Default | Meaning |
| --- | --- | --- |
| `PHONEBRIDGE_PHONE_ADDRESS` | first paired iPhone | Bluetooth address for phone mode |
| `PHONEBRIDGE_PORT` | `8765` | Local port for the interface |
| `PHONEBRIDGE_HOST` | `127.0.0.1` | Set to `0.0.0.0` to open it from other devices (requires a token) |
| `PHONEBRIDGE_TOKEN` | none | Access token for remote use: open `http://<pc>:8765/?token=<token>` |
| `PHONEBRIDGE_DATA_DIR` | `%LOCALAPPDATA%\Phonebridge` | History, settings and log |

The Mac connection is set in the app and stored in `config.json` in the data folder, with the password
encrypted by Windows (DPAPI) so only your Windows account can read it. A copy is kept in your user registry
(`HKCU\Software\Phonebridge`), so the connection is restored automatically if the data folder is lost.
Uninstalling with "delete history" removes both.

## Privacy

Phonebridge only listens on `127.0.0.1` unless you change `PHONEBRIDGE_HOST` (then a token is required),
and blocks requests from other websites. In Mac mode, a copy of your chats, messages, contacts and the
photos and files you open is kept in `%LOCALAPPDATA%\Phonebridge` (`mac-cache.db` and `attachments\`), so
conversations stay readable and searchable while the Mac is off or unreachable. In phone mode, texts are
saved in `phonebridge.db` there. Uninstalling with "delete history" removes all of it.

## Development

```bat
ruff check --fix .
ruff format .
ty check
pytest
```

- `src/phonebridge/services/`: `mac_service.py` (Mac/BlueBubbles backend), `phone_service.py` and
  `bridge.py` (Bluetooth backend), `manager.py` (switches between them), `messaging.py` (the interface both implement).
- `src/phonebridge/mac/`: BlueBubbles REST client, Socket.IO events, payload models and conversions.
- `src/phonebridge/protocol/`, `bluetooth/`, `storage/`: Bluetooth MAP/PBAP stack and SQLite history.
- `src/phonebridge/web/`: FastAPI + WebSocket API (`docs/ui-api.md`) and the interface.
- `src/phonebridge/shell/`: desktop app (WebView2 window, tray, notifications, install/uninstall).
- `packaging/`: app icon, PyInstaller specs, build script, and the Mac setup script.
