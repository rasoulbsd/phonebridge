#!/bin/bash
# Installs and configures BlueBubbles Server on this Mac as Phonebridge's iMessage bridge.
#
# Safe to run more than once. It never prints the server password: a random one is
# written to "Phonebridge Mac password.txt" on the Desktop (readable only by you), and
# you type it into Phonebridge's Settings yourself.
#
# Usage:  bash setup-bluebubbles.sh            # install + basic settings
#         ENABLE_PRIVATE_API=1 bash setup-bluebubbles.sh   # after turning SIP off
set -euo pipefail

APP="/Applications/BlueBubbles.app"
WORK="$HOME/Library/Caches/phonebridge-setup"
PASSWORD_FILE="$HOME/Desktop/Phonebridge Mac password.txt"
PORT=1234
RELEASES="https://api.github.com/repos/BlueBubblesApp/bluebubbles-server/releases/latest"

say() { printf '==> %s\n' "$*"; }

quit_server() {
    if pgrep -x BlueBubbles >/dev/null; then
        osascript -e 'tell application "BlueBubbles" to quit' >/dev/null 2>&1 || true
        for _ in $(seq 1 30); do pgrep -x BlueBubbles >/dev/null || return 0; sleep 1; done
        pkill -x BlueBubbles || true
    fi
}

find_config() {
    find "$HOME/Library/Application Support" -maxdepth 3 -name config.db -path '*luebubbles*' 2>/dev/null | head -1
}

# 1. Install the app (Apple silicon and Intel builds are separate downloads).
mkdir -p "$WORK"
if [ ! -d "$APP" ]; then
    say "Downloading BlueBubbles Server"
    urls=$(curl -fsSL "$RELEASES" | grep -o '"browser_download_url": *"[^"]*\.dmg"' | sed -E 's/.*"(https[^"]*)"/\1/')
    if [ "$(uname -m)" = "arm64" ]; then
        url=$(printf '%s\n' "$urls" | grep -- '-arm64\.dmg$' | head -1)
    else
        url=$(printf '%s\n' "$urls" | grep -v -- '-arm64\.dmg$' | head -1)
    fi
    [ -n "$url" ] || { echo "Couldn't find a download for this Mac." >&2; exit 1; }
    curl -fL --retry 3 --silent --show-error -o "$WORK/BlueBubbles.dmg" "$url"
    mount=$(hdiutil attach -nobrowse -readonly "$WORK/BlueBubbles.dmg" | awk -F'\t' '/\/Volumes\//{print $NF}' | tail -1)
    ditto "$mount/BlueBubbles.app" "$APP"
    hdiutil detach "$mount" -quiet
    rm -f "$WORK/BlueBubbles.dmg"
fi
say "BlueBubbles Server $(defaults read "$APP/Contents/Info" CFBundleShortVersionString) is installed"

# 2. First launch creates the settings database.
config=$(find_config)
if [ -z "$config" ]; then
    say "Starting it once to create its settings"
    open -gj -a "$APP"
    for _ in $(seq 1 90); do config=$(find_config); [ -n "$config" ] && break; sleep 1; done
    [ -n "$config" ] || { echo "BlueBubbles didn't create its settings. Open it once by hand, then rerun." >&2; exit 1; }
    sleep 3
fi
quit_server

# 3. Settings: LAN only on port 1234, start at login, keep the Mac awake, FaceTime events on.
set_config() { sqlite3 "$config" "INSERT OR REPLACE INTO config(name, value) VALUES('$1', '$2');"; }
set_config socket_port "$PORT"
set_config proxy_service "lan-url"
set_config auto_caffeinate 1
set_config auto_start 1
set_config auto_start_method "launch-agent"
set_config start_minimized 1
set_config check_for_updates 1
set_config auto_install_updates 0
set_config facetime_calling 1
set_config tutorial_is_done 1
if [ "${ENABLE_PRIVATE_API:-0}" = "1" ]; then
    set_config enable_private_api 1
    set_config enable_ft_private_api 1
    say "Private API turned on"
fi

# 4. Password: random, kept in a Desktop file only you can read, never printed.
if [ ! -s "$PASSWORD_FILE" ]; then
    # openssl writes a fixed amount and exits, so nothing in this pipeline gets SIGPIPE under pipefail.
    (umask 077; openssl rand -base64 48 | LC_ALL=C tr -dc 'A-Za-z0-9' | cut -c1-24 >"$PASSWORD_FILE")
    say "Created a server password in \"Phonebridge Mac password.txt\" on your Desktop"
fi
printf "INSERT OR REPLACE INTO config(name, value) VALUES('password', '%s');" "$(cat "$PASSWORD_FILE")" | sqlite3 "$config"

# 5. Start it and wait for the port.
open -gj -a "$APP"
for _ in $(seq 1 60); do nc -z 127.0.0.1 "$PORT" 2>/dev/null && break; sleep 1; done
if nc -z 127.0.0.1 "$PORT" 2>/dev/null; then
    ip=$(ipconfig getifaddr en0 || ipconfig getifaddr en1 || echo "<this Mac's IP>")
    say "BlueBubbles Server is running. Phonebridge address: http://$ip:$PORT"
else
    say "Started, but port $PORT isn't open yet. Grant Full Disk Access to BlueBubbles, then reopen it."
fi
