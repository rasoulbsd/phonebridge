#!/usr/bin/env bash
# Builds PhonebridgeFaceTime.dylib for Intel Macs on Linux/WSL, without the macOS SDK.
# Needs clang and lld (Ubuntu: apt install clang lld).
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
linker=$(command -v ld64.lld || command -v ld64.lld-18 || command -v ld64.lld-17 || command -v ld64.lld-14 || true)
compiler=$(command -v clang || command -v clang-18 || command -v clang-17 || command -v clang-14 || true)
if [ -z "$linker" ] || [ -z "$compiler" ]; then
    echo "Needs clang and ld64.lld (apt install clang lld)." >&2
    exit 1
fi

# clang looks for the Mach-O linker under the name ld64.lld.
bin=$(mktemp -d)
trap 'rm -rf "$bin"' EXIT
ln -s "$linker" "$bin/ld64.lld"

# The system frameworks are already loaded in FaceTime, so symbols resolve at load time.
PATH="$bin:$PATH" "$compiler" \
    -target x86_64-apple-macos12.0 -fblocks -O2 -Wall -Wextra -Werror \
    -dynamiclib -nostdlib -nostdinc -fuse-ld=lld \
    -Wl,-undefined,dynamic_lookup -Wl,-install_name,@rpath/PhonebridgeFaceTime.dylib \
    -o "$here/PhonebridgeFaceTime.dylib" "$here/phonebridge_facetime.c"
echo "Built $here/PhonebridgeFaceTime.dylib"
