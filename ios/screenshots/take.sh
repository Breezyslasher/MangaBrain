#!/usr/bin/env bash
# Takes the app's screenshots in the iOS Simulator, against mock_server.py.
# Usage: take.sh <path to MangaBrain.app built for the simulator> <out dir>
set -euo pipefail
APP="$1"
OUT="$2"
BUNDLE=io.github.breezyslasher.mangabrain
mkdir -p "$OUT"

pick() {  # first available simulator whose name starts with $1
  xcrun simctl list devices available -j | python3 -c '
import json, sys
prefix = sys.argv[1]
for runtime, devices in json.load(sys.stdin)["devices"].items():
    if "iOS" not in runtime:
        continue
    for d in devices:
        if d["name"].startswith(prefix):
            print(d["udid"]); sys.exit()
' "$1"
}

shoot() {  # device, file name, launch arguments...
  local dev="$1" name="$2"; shift 2
  xcrun simctl terminate "$dev" "$BUNDLE" >/dev/null 2>&1 || true
  xcrun simctl launch "$dev" "$BUNDLE" -server.url http://127.0.0.1:8009 "$@"
  sleep 9
  xcrun simctl io "$dev" screenshot "$OUT/$name.png"
  echo "took $name"
}

run_on() {  # device udid, prefix
  local dev="$1" tag="$2"
  xcrun simctl boot "$dev" || true
  xcrun simctl bootstatus "$dev" -b
  xcrun simctl status_bar "$dev" override --time 9:41 --batteryState charged --batteryLevel 100 \
    --cellularBars 4 --wifiBars 3 || true
  xcrun simctl install "$dev" "$APP"
  # The first launch also warms the image cache for the later ones.
  shoot "$dev" "$tag-1-discover" -ui.tab discover -ui.browse YES
  shoot "$dev" "$tag-1-discover" -ui.tab discover -ui.browse YES
  shoot "$dev" "$tag-2-seed" -ui.tab discover -ui.seed 0
  shoot "$dev" "$tag-3-filters" -ui.tab filters
  shoot "$dev" "$tag-4-mix" -ui.tab mix -ui.mix YES
  shoot "$dev" "$tag-5-settings" -ui.tab settings
  xcrun simctl shutdown "$dev" || true
}

PHONE=$(pick "iPhone 16 Pro")
[ -n "$PHONE" ] || PHONE=$(pick "iPhone")
run_on "$PHONE" iphone

IPAD=$(pick "iPad Pro 13")
[ -n "$IPAD" ] || IPAD=$(pick "iPad")
[ -n "$IPAD" ] && run_on "$IPAD" ipad
ls -la "$OUT"
