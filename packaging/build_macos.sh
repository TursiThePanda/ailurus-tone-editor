#!/bin/zsh
# Build "Ailurus Tone Editor.app" and a DMG into dist/.
#   packaging/build_macos.sh
# Needs the dev venv (.venv with pyinstaller) and, for the layered icon, Xcode's actool.
# AILURUS_FEED_URL and AILURUS_SPARKLE_PUBLIC_KEY override the update feed and its key
# (local update tests only).
set -euo pipefail
ROOT=${0:A:h:h}
cd "$ROOT"
PY=${PY:-.venv/bin/python}
VERSION=$($PY -c 'import tomllib;print(tomllib.load(open("pyproject.toml","rb"))["project"]["version"])')
APP="dist/Ailurus Tone Editor.app"
BUNDLE_ID=io.github.tursithepanda.ailurus-tone-editor

# --- Sparkle (update framework), pinned by checksum ---------------------------------
SPARKLE_VER=2.10.1
SPARKLE_SHA256=1a856d2a9edb8e628de88121d7be04cf0e2167f8641650b78fca00b14244cf27
SPK=build/sparkle
if [[ ! -d $SPK/Sparkle.framework ]]; then
    txz=build/downloads/Sparkle-$SPARKLE_VER.tar.xz
    mkdir -p build/downloads
    [[ -f $txz ]] || curl -fsSL -o "$txz" \
        "https://github.com/sparkle-project/Sparkle/releases/download/$SPARKLE_VER/Sparkle-$SPARKLE_VER.tar.xz"
    [[ $(shasum -a 256 "$txz" | cut -d' ' -f1) == "$SPARKLE_SHA256" ]] || { echo "Sparkle checksum mismatch"; exit 1; }
    rm -rf $SPK && mkdir -p $SPK && tar -xJf "$txz" -C $SPK
fi

# --- app ----------------------------------------------------------------------------
# Sparkle compares CFBundleVersion, so it grows with every commit; macOS also caches icons
# per bundle id and version.
export AILURUS_BUILD_VERSION="$VERSION.$(git rev-list --count HEAD 2>/dev/null || echo 0)"
$PY tools/make_icon.py
rm -rf build/pyinstaller "$APP"
$PY -m PyInstaller --noconfirm --clean --workpath build/pyinstaller --distpath dist packaging/ailurus-tone.spec

# Layered icon for macOS 26+ (actool output); the .icns stays for older macOS.
if [[ -f assets/build/Assets.car ]]; then
    cp assets/build/Assets.car "$APP/Contents/Resources/"
fi
ditto $SPK/Sparkle.framework "$APP/Contents/Frameworks/Sparkle.framework"
cp $SPK/LICENSE "$APP/Contents/Resources/Sparkle-LICENSE.txt"
cp LICENSE "$APP/Contents/Resources/"

# Ad-hoc signature (no Developer ID yet). Extended attributes make codesign refuse.
# Sparkle accepts an update only if it is signed "the same" as the running app; an ad-hoc
# signature's default requirement is the cdhash, which changes with every build, so the
# outer signature pins the requirement to the bundle identifier instead.
xattr -cr "$APP"
codesign --force --deep --sign - "$APP"
codesign --force --sign - -r="designated => identifier \"$BUNDLE_ID\"" "$APP"
codesign --verify --deep --strict "$APP"

# --- DMG ----------------------------------------------------------------------------
DMG="dist/Ailurus-Tone-Editor-$VERSION.dmg"
rm -f "$DMG" "$DMG.sha256"
STAGE=$(mktemp -d)
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -quiet -volname "Ailurus Tone Editor $VERSION" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"
# the checksum file names only the file (no local path)
(cd dist && shasum -a 256 "${DMG:t}" | tee "${DMG:t}.sha256")
echo "built: $APP ($AILURUS_BUILD_VERSION), $DMG"
