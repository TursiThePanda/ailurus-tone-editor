#!/usr/bin/env bash
# Build Ailurus Tone Editor as a Linux AppImage into dist/ (x86_64).
#   packaging/build_linux.sh
# Needs the dev venv (.venv with pyinstaller and pillow). Downloads innoextract, appimagetool and
# the AppImage runtime, each pinned by checksum. The UI opens in the user's browser (no GTK/Qt).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
PY=${PY:-.venv/bin/python}
VERSION=$($PY -c 'import tomllib;print(tomllib.load(open("pyproject.toml","rb"))["project"]["version"])')
COUNT=$(git rev-list --count HEAD 2>/dev/null || echo 0)
export AILURUS_BUILD_VERSION="$VERSION.$COUNT"
DL=build/downloads
mkdir -p "$DL"

fetch() {   # fetch <url> <file> <sha256>
    [[ -f "$DL/$2" ]] || curl -fsSL -o "$DL/$2" "$1"
    echo "$3  $DL/$2" | sha256sum -c --quiet - || { echo "checksum mismatch: $2"; rm -f "$DL/$2"; exit 1; }
}

# --- innoextract 1.9 (zlib licence): unpacks the BTS Windows installer --------------------
fetch https://github.com/dscharrer/innoextract/releases/download/1.9/innoextract-1.9-linux.tar.xz \
    innoextract-1.9-linux.tar.xz 008efe5011476ccc4aae17c3e22038b5a1bc5c7aad2b9d4d869537bf3874d21f
rm -rf build/innoextract && mkdir -p build/innoextract
tar -xJf "$DL/innoextract-1.9-linux.tar.xz" -C build/innoextract --strip-components=1
cp build/innoextract/bin/amd64/innoextract build/innoextract/innoextract
cp build/innoextract/license/innoextract.LICENSE build/innoextract/innoextract.LICENSE

# --- appimagetool 1.9.1 and the type 2 runtime (static, no libfuse2 needed on the host) -----
fetch https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage \
    appimagetool-1.9.1-x86_64.AppImage ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0
fetch https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-x86_64 \
    runtime-x86_64-20251108 2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d
chmod +x "$DL/appimagetool-1.9.1-x86_64.AppImage"

# --- app ------------------------------------------------------------------------------------
rm -rf "dist/Ailurus Tone Editor"
$PY -m PyInstaller --noconfirm --clean packaging/ailurus-tone.spec

APPDIR=build/AppDir
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/lib" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp -a "dist/Ailurus Tone Editor" "$APPDIR/usr/lib/ailurus-tone-editor"
cp LICENSE "$APPDIR/usr/lib/ailurus-tone-editor/LICENSE.txt"
$PY - <<'EOF'
from PIL import Image
Image.open("assets/build/icon-1024.png").resize((256, 256), Image.LANCZOS).save(
    "build/AppDir/usr/share/icons/hicolor/256x256/apps/ailurus-tone-editor.png")
EOF
cp "$APPDIR/usr/share/icons/hicolor/256x256/apps/ailurus-tone-editor.png" "$APPDIR/ailurus-tone-editor.png"
cat > "$APPDIR/ailurus-tone-editor.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Ailurus Tone Editor
Comment=Editor and MCP server for the BOSS GX-10 and KATANA Gen 3
Exec=ailurus-tone-editor
Icon=ailurus-tone-editor
Categories=AudioVideo;Audio;Music;
Terminal=false
X-AppImage-Version=$VERSION
EOF
cp "$APPDIR/ailurus-tone-editor.desktop" "$APPDIR/usr/share/applications/"
cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE=$(dirname "$(readlink -f "$0")")
exec "$HERE/usr/lib/ailurus-tone-editor/Ailurus Tone Editor" "$@"
EOF
chmod +x "$APPDIR/AppRun"

OUT="dist/Ailurus-Tone-Editor-$VERSION-x86_64.AppImage"
rm -f "$OUT"
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$DL/appimagetool-1.9.1-x86_64.AppImage" --no-appstream \
    --runtime-file "$DL/runtime-x86_64-20251108" "$APPDIR" "$OUT"
(cd dist && sha256sum "$(basename "$OUT")" > "$(basename "$OUT").sha256" && cat "$(basename "$OUT").sha256")
echo "built: $OUT ($AILURUS_BUILD_VERSION)"
