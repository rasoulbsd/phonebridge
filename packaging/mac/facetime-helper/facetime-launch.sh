#!/bin/zsh
# Started by launchd (com.phonebridge.facetime). Runs FaceTime with BlueBubbles' FaceTime helper
# and Phonebridge's call helper loaded. The BlueBubbles password is read on every launch so it
# always matches the server, and is never written anywhere.
set -u

support="$HOME/Library/Application Support/Phonebridge"
config="$HOME/Library/Application Support/bluebubbles-server/config.db"
bluebubbles_helper="/Applications/BlueBubbles.app/Contents/Resources/appResources/private-api/macos11/BlueBubblesFaceTimeHelper.dylib"

libraries="$support/PhonebridgeFaceTime.dylib"
[ -f "$bluebubbles_helper" ] && libraries="$bluebubbles_helper:$libraries"

PHONEBRIDGE_TOKEN=$(/usr/bin/sqlite3 -readonly "$config" "SELECT value FROM config WHERE name = 'password';" 2>/dev/null)
export PHONEBRIDGE_TOKEN
export DYLD_INSERT_LIBRARIES="$libraries"
exec /System/Applications/FaceTime.app/Contents/MacOS/FaceTime
