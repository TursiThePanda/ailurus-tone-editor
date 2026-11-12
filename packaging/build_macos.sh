#!/bin/zsh
# Build "Ailurus Tone Editor.app" and a DMG into dist/.
#   packaging/build_macos.sh
# Needs the dev venv (.venv with pyinstaller) and, for the layered icon, Xcode's actool.
set -euo pipefail
ROOT=${0:A:h:h}
cd "$ROOT"
PY=.venv/bin/python
VERSION=$($PY -c 'import tomllib;print(tomllib.load(open("pyproject.toml","rb"))["project"]["version"])')
APP="dist/Ailurus Tone Editor.app"

$PY tools/make_icon.py
rm -rf build/pyinstaller "$APP"
$PY -m PyInstaller --noconfirm --clean --workpath build/pyinstaller --distpath dist packaging/ailurus-tone.spec

# Layered icon for macOS 26+ (actool output); the .icns stays for older macOS.
if [[ -f assets/build/Assets.car ]]; then
    cp assets/build/Assets.car "$APP/Contents/Resources/"
fi
# Ad-hoc signature over the finished bundle (no Developer ID yet).
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

DMG="dist/Ailurus-Tone-Editor-$VERSION.dmg"
rm -f "$DMG"
STAGE=$(mktemp -d)
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -quiet -volname "Ailurus Tone Editor" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"
shasum -a 256 "$DMG"
echo "built: $APP, $DMG"
