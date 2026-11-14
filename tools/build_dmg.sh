#!/usr/bin/env bash
# Builds a self-contained "Handsight.app" and "Handsight-<version>.dmg" in dist/.
# Python, the libraries and the models are inside the app, so it runs on any Apple
# Silicon Mac with macOS 13 or later, with nothing to install.
#
# Usage: tools/build_dmg.sh [version]          (default version: 1.0)
#
# Signing, picked automatically:
#   - a "Developer ID Application" certificate in your keychain: signed with the hardened
#     runtime, then notarized and stapled if the notary profile exists (see below).
#   - none: ad-hoc signed. Works on this Mac; on other Macs people must click Open Anyway
#     in System Settings > Privacy & Security the first time.
# Override with SIGN_IDENTITY="<name or hash>" (use "-" for ad-hoc).
#
# Notarization needs a keychain profile, made once with:
#   xcrun notarytool store-credentials deskguard --apple-id <apple id> --team-id <team id>
# Use NOTARY_PROFILE=<name> for a different profile name, NOTARIZE=0 to skip it.
set -euo pipefail

PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${1:-1.0}"
[[ "$VERSION" =~ ^[0-9]+(\.[0-9]+){0,2}$ ]] || { echo "Version must look like 1.0 or 1.2.3"; exit 1; }
# Not the checkout build's ID (local.handsight.dev): macOS keeps camera and
# Accessibility permissions per bundle ID, so the two builds must not share one.
BUNDLE_ID="io.github.abhi183.handsight"
PY_VERSION="3.12"
NOTARY_PROFILE="${NOTARY_PROFILE:-deskguard}"
DIST="$PROJECT/dist"
BUILD="$DIST/build"
APP="$BUILD/Handsight.app"
RES="$APP/Contents/Resources"
PYDIR="$RES/python"
PY="$PYDIR/bin/python$PY_VERSION"
DMG="$DIST/Handsight-$VERSION.dmg"
ENTITLEMENTS="$PROJECT/tools/entitlements.plist"
MODELS=(face_detection_yunet.onnx face_recognition_sface_2021dec.onnx
        gesture_recognizer.task selfie_segmenter.tflite)

step() { printf '\n==> %s\n' "$*"; }
die() { echo "error: $*" >&2; exit 1; }

check_tools() {
  [ "$(uname -m)" = arm64 ] || die "build on an Apple Silicon Mac"
  for tool in uv clang codesign hdiutil install_name_tool ditto; do
    command -v "$tool" >/dev/null || die "$tool not found"
  done
  for model in "${MODELS[@]}"; do
    [ -f "$PROJECT/models/$model" ] || die "models/$model missing; run ./run desk once first"
  done
}

pick_identity() {
  if [ -n "${SIGN_IDENTITY:-}" ]; then echo "$SIGN_IDENTITY"; return; fi
  local found
  found="$(security find-identity -v -p codesigning |
           sed -n 's/.*"\(Developer ID Application: [^"]*\)".*/\1/p' | head -1)"
  echo "${found:--}"
}

copy_python() {
  step "Copying Python $PY_VERSION"
  local src
  src="$(uv python find --managed-python "$PY_VERSION")" || die "uv has no Python $PY_VERSION"
  src="$(cd "$(dirname "$(readlink -f "$src")")/.." && pwd)"
  mkdir -p "$RES"
  ditto "$src" "$PYDIR"
  rm -f "$PYDIR"/lib/python$PY_VERSION/EXTERNALLY-MANAGED
  # Point libpython at itself relative to whoever loads it, not the uv folder.
  install_name_tool -id "@rpath/libpython$PY_VERSION.dylib" "$PYDIR/lib/libpython$PY_VERSION.dylib"
}

install_packages() {
  step "Installing packages"
  local reqs="$BUILD/requirements.txt"
  # pytest is for development; mediapipe goes in on its own below.
  grep -vE '^(pytest|mediapipe)\b' "$PROJECT/requirements.txt" > "$reqs"
  # What mediapipe needs for the vision tasks Handsight uses. Its jax, jaxlib,
  # sentencepiece and sounddevice requirements are for other tasks and add ~350 MB.
  # matplotlib stays: `import mediapipe` imports it.
  uv pip install -q --python "$PY" -r "$reqs" \
    absl-py 'attrs>=19.1.0' 'flatbuffers>=2.0' 'protobuf>=4.25.3,<5' matplotlib
  uv pip install -q --python "$PY" --no-deps "$(grep -E '^mediapipe\b' "$PROJECT/requirements.txt")"
}

trim_python() {
  step "Removing what the app never uses"
  local lib="$PYDIR/lib/python$PY_VERSION"
  rm -rf "$PYDIR/include" "$PYDIR/share" "$PYDIR/lib/pkgconfig" \
         "$lib/test" "$lib/idlelib" "$lib/turtledemo" "$lib/ensurepip" "$lib/lib2to3" \
         "$lib/site-packages/PyObjCTest" "$lib/site-packages/pip" \
         "$lib/site-packages/pip-"*.dist-info
  find "$PYDIR" -name __pycache__ -type d -prune -exec rm -rf {} +
  find "$lib/site-packages" -type d \( -name tests -o -name test \) -prune -exec rm -rf {} +
}

copy_app_code() {
  step "Copying Handsight"
  mkdir -p "$RES/app/models" "$RES/app/assets"
  rsync -a --exclude __pycache__ "$PROJECT/handsight" "$RES/app/"
  for model in "${MODELS[@]}"; do cp "$PROJECT/models/$model" "$RES/app/models/"; done
  cp "$PROJECT/assets/icon.png" "$RES/app/assets/"
  cp "$PROJECT/assets/AppIcon.icns" "$RES/AppIcon.icns"
  cp "$PROJECT/assets/Credits.html" "$RES/Credits.html"  # shown in About Handsight
  cp "$PROJECT/LICENSE" "$PROJECT/NOTICE" "$RES/"
  # Precompiled once here; the signed app can't write .pyc files at run time.
  "$PY" -m compileall -q -j 0 --invalidation-mode unchecked-hash "$PYDIR/lib" "$RES/app" >/dev/null
}

write_info_plist() {
  cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Handsight</string>
  <key>CFBundleDisplayName</key><string>Handsight</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundleExecutable</key><string>Handsight</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSArchitecturePriority</key><array><string>arm64</string></array>
  <key>LSRequiresNativeExecution</key><true/>
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
}

build_launcher() {
  step "Building the launcher"
  local include="$1"
  mkdir -p "$APP/Contents/MacOS"
  clang -O2 -Wall -mmacosx-version-min=13.0 -DBUNDLED -I "$include" \
    "$PROJECT/tools/launcher.c" -L "$PYDIR/lib" -lpython$PY_VERSION \
    -Wl,-rpath,@executable_path/../Resources/python/lib \
    -o "$APP/Contents/MacOS/Handsight"
}

check_no_build_paths() {
  # Every library must load from inside the app, never from this Mac's uv or Homebrew.
  local bad
  bad="$(find "$APP" -type f \( -name '*.so' -o -name '*.dylib' -o -perm -u+x \) -print0 |
         xargs -0 otool -L 2>/dev/null |
         grep -E "^\s+($HOME|/opt/homebrew|/usr/local)" | sort -u || true)"
  [ -z "$bad" ] || die "libraries that point outside the app:"$'\n'"$bad"
}

sign_app() {
  local identity="$1"
  step "Signing (${identity})"
  local opts=(--force --sign "$identity")
  [ "$identity" = "-" ] || opts+=(--options runtime --timestamp)
  # Inside out: every library and executable first, then the app itself.
  find "$RES" -type f -print0 | while IFS= read -r -d '' f; do
    if file -b "$f" | grep -q 'Mach-O'; then
      codesign "${opts[@]}" "$f" 2>&1 | grep -v "replacing existing signature" || true
    fi
  done
  codesign "${opts[@]}" --entitlements "$ENTITLEMENTS" "$APP"
  codesign --verify --strict --deep "$APP"
}

notarize() {
  local target="$1"
  step "Notarizing $(basename "$target") (takes a few minutes)"
  xcrun notarytool submit "$target" --keychain-profile "$NOTARY_PROFILE" --wait
}

can_notarize() {
  local identity="$1"
  [ "${NOTARIZE:-1}" = 1 ] || return 1
  [[ "$identity" == "Developer ID Application:"* ]] || return 1
  xcrun notarytool history --keychain-profile "$NOTARY_PROFILE" >/dev/null 2>&1
}

make_dmg() {
  local identity="$1"
  step "Making the disk image"
  local stage="$BUILD/dmg"
  rm -rf "$stage" "$DMG"
  mkdir -p "$stage"
  ditto "$APP" "$stage/Handsight.app"
  ln -s /Applications "$stage/Applications"
  hdiutil create -quiet -volname "Handsight" -srcfolder "$stage" -fs HFS+ \
    -format ULFO -o "$DMG"
  [ "$identity" = "-" ] || codesign --force --sign "$identity" --timestamp "$DMG"
}

main() {
  check_tools
  local identity
  identity="$(pick_identity)"
  rm -rf "$BUILD"
  mkdir -p "$BUILD"

  copy_python
  local include="$BUILD/include"
  ditto "$PYDIR/include/python$PY_VERSION" "$include"  # trim_python removes it from the app
  install_packages
  trim_python
  copy_app_code
  write_info_plist
  build_launcher "$include"
  check_no_build_paths
  sign_app "$identity"

  if can_notarize "$identity"; then
    local zip="$BUILD/Handsight.zip"
    ditto -c -k --keepParent "$APP" "$zip"
    notarize "$zip"
    xcrun stapler staple "$APP"
    make_dmg "$identity"
    notarize "$DMG"
    xcrun stapler staple "$DMG"
    NOTARIZED=yes
  else
    make_dmg "$identity"
    NOTARIZED=no
  fi

  step "Done"
  echo "App:        $APP ($(du -sh "$APP" | cut -f1))"
  echo "Disk image: $DMG ($(du -sh "$DMG" | cut -f1))"
  echo "Signed by:  $identity"
  echo "Notarized:  $NOTARIZED"
  if [ "$NOTARIZED" = no ]; then
    echo "Not notarized: other Macs need System Settings > Privacy & Security > Open Anyway once."
  fi
}

main "$@"
