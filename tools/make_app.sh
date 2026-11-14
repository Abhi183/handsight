#!/usr/bin/env bash
# Builds "Handsight.app". Its executable is a small C program (tools/launcher.c) that
# runs the Python app inside its own process, so macOS treats Handsight as a real app:
# its own Dock icon, name, menu bar and permissions.
# Usage: tools/make_app.sh [destination dir]   (default: ~/Desktop)
#
# The app uses the project in the folder that contains it if there is one, else the
# project it was built from. Code changes don't need a rebuild; a new venv Python does.
set -euo pipefail

PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${1:-$HOME/Desktop}"
APP="$DEST/Handsight.app"
ICNS="$PROJECT/assets/AppIcon.icns"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Handsight</string>
  <key>CFBundleDisplayName</key><string>Handsight</string>
  <key>CFBundleIdentifier</key><string>local.handsight.dev</string>
  <key>CFBundleExecutable</key><string>Handsight</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSUIElement</key><false/>
  <key>NSPrincipalClass</key><string>NSApplication</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSApplicationCategoryType</key><string>public.app-category.utilities</string>
  <key>NSHumanReadableCopyright</key><string>© 2026 Abhishek Shekhar. Apache License 2.0.</string>
  <key>NSAppleEventsUsageDescription</key>
  <string>Handsight switches browser tabs and presses shortcuts for your gestures, and can close personal tabs when someone sits with you.</string>
  <key>NSCameraUsageDescription</key>
  <string>Handsight reads your hand gestures so you can control your Mac without touching it. It can also lock the Mac when you leave and warn you when someone looks over your shoulder.</string>
</dict>
</plist>
PLIST

PY="$PROJECT/.venv/bin/python"
[ -x "$PY" ] || { echo "Run ./run desk once first to create the venv."; exit 1; }
PYBIN="$(dirname "$(readlink -f "$PY")")"
PYCONFIG="$PYBIN/python3.12-config"
PYLIB="$("$PY" -c 'import sysconfig; print(sysconfig.get_config_var("LIBDIR"))')"
[ -x "$PYCONFIG" ] || { echo "No python3.12-config next to $PYBIN"; exit 1; }
clang -O2 -Wall $("$PYCONFIG" --includes) -DPROJECT_DIR="\"$PROJECT\"" \
  "$PROJECT/tools/launcher.c" -L "$PYLIB" -lpython3.12 -Wl,-rpath,"$PYLIB" \
  -o "$APP/Contents/MacOS/Handsight"

# Icon: use the committed .icns; regenerate it only if missing.
if [ ! -f "$ICNS" ]; then
  PY="$PROJECT/.venv/bin/python"
  [ -x "$PY" ] || { echo "Run ./run desk once first to create the venv."; exit 1; }
  TMP="$(mktemp -d)"
  "$PY" "$PROJECT/tools/make_icon.py" "$TMP/icon.png"
  mkdir "$TMP/AppIcon.iconset"
  for s in 16 32 128 256 512; do
    sips -z $s $s "$TMP/icon.png" --out "$TMP/AppIcon.iconset/icon_${s}x${s}.png" >/dev/null
    sips -z $((s*2)) $((s*2)) "$TMP/icon.png" --out "$TMP/AppIcon.iconset/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$TMP/AppIcon.iconset" -o "$ICNS"
  rm -rf "$TMP"
fi
cp "$ICNS" "$APP/Contents/Resources/AppIcon.icns"

# Ad-hoc signature so macOS remembers the camera permission for this app.
codesign --force --deep -s - "$APP" >/dev/null 2>&1 || echo "warning: codesign failed"
touch "$APP"
echo "Built: $APP"
