#!/bin/zsh
# Installs Phonebridge's FaceTime call helper on this Mac, so the Windows app can ring people
# directly and move answered calls to the PC. Run it from this folder, next to
# PhonebridgeFaceTime.dylib. Safe to run again; "--uninstall" puts everything back.
#
# What it changes:
#   - copies the helper to ~/Library/Application Support/Phonebridge
#   - adds a login item (~/Library/LaunchAgents/com.phonebridge.facetime.plist) that keeps
#     FaceTime running with BlueBubbles' FaceTime helper and this one loaded
#   - tells BlueBubbles not to start FaceTime itself (it would start it without this helper)
set -eu

label="com.phonebridge.facetime"
here=${0:A:h}
support="$HOME/Library/Application Support/Phonebridge"
agent="$HOME/Library/LaunchAgents/$label.plist"
config="$HOME/Library/Application Support/bluebubbles-server/config.db"
domain="gui/$(id -u)"
port=1235

set_bluebubbles_facetime() {
    [ -f "$config" ] || return 0
    /usr/bin/sqlite3 "$config" "INSERT OR REPLACE INTO config(name, value) VALUES('enable_ft_private_api', '$1');"
}

restart_bluebubbles() {
    pkill -x BlueBubbles 2>/dev/null || true
    for _ in {1..20}; do pgrep -x BlueBubbles >/dev/null || break; sleep 0.5; done
    "$@"
    for _ in {1..5}; do open -a BlueBubbles 2>/dev/null && return 0; sleep 2; done
    echo "Couldn't reopen BlueBubbles. Open it by hand." >&2
}

stop_agent() {
    launchctl bootout "$domain/$label" 2>/dev/null || true
    pkill -x FaceTime 2>/dev/null || true
}

if [ "${1:-}" = "--uninstall" ]; then
    stop_agent
    rm -f "$agent" "$support/PhonebridgeFaceTime.dylib" "$support/facetime-launch.sh"
    restart_bluebubbles set_bluebubbles_facetime 1
    echo "Removed the Phonebridge FaceTime helper."
    exit 0
fi

if [ "$(uname -m)" != "x86_64" ]; then
    echo "This build of the helper is for Intel Macs only." >&2
    exit 1
fi
[ -f "$here/PhonebridgeFaceTime.dylib" ] || { echo "PhonebridgeFaceTime.dylib is missing next to this script." >&2; exit 1; }
[ -f "$config" ] || { echo "Set up BlueBubbles first (setup-bluebubbles.sh)." >&2; exit 1; }

mkdir -p "$support" "$HOME/Library/LaunchAgents"
install -m 0644 "$here/PhonebridgeFaceTime.dylib" "$support/PhonebridgeFaceTime.dylib"
install -m 0700 "$here/facetime-launch.sh" "$support/facetime-launch.sh"

cat > "$agent" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$label</string>
    <key>ProgramArguments</key>
    <array><string>/bin/zsh</string><string>$support/facetime-launch.sh</string></array>
    <key>EnvironmentVariables</key>
    <dict><key>PHONEBRIDGE_PORT</key><string>$port</string></dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>10</integer>
    <key>LimitLoadToSessionType</key><string>Aqua</string>
    <key>ProcessType</key><string>Interactive</string>
</dict>
</plist>
PLIST
chmod 0644 "$agent"

stop_agent
restart_bluebubbles set_bluebubbles_facetime 0
launchctl bootstrap "$domain" "$agent"

password=$(/usr/bin/sqlite3 -readonly "$config" "SELECT value FROM config WHERE name = 'password';")
for _ in {1..30}; do
    if curl -fsS -m 2 -H "X-Phonebridge-Token: $password" "http://127.0.0.1:$port/health" >/dev/null 2>&1; then
        echo "The Phonebridge FaceTime helper is running on port $port."
        exit 0
    fi
    sleep 1
done
echo "The helper didn't start. Check Console.app for messages starting with 'Phonebridge:'." >&2
exit 1
