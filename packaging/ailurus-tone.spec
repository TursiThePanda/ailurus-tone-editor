# PyInstaller spec for Ailurus Tone Editor: macOS .app (packaging/build_macos.sh) or a Windows
# folder for the installer (packaging/build_windows.ps1). PyInstaller cannot cross-compile.
import json
import os
import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
BUNDLE_ID = "io.github.tursithepanda.ailurus-tone-editor"
# one source for the update feed and its EdDSA public key: ailurus_tone/updater.py
sys.path.insert(0, str(ROOT))
from ailurus_tone.updater import FEED_URL, PUBLIC_KEY as SPARKLE_PUBLIC_KEY  # noqa: E402
# Sparkle/WinSparkle compare this: version + commit count, set by build_macos.sh / build_windows.ps1
BUILD_VERSION = os.environ.get("AILURUS_BUILD_VERSION") or VERSION
(ROOT / "build").mkdir(exist_ok=True)
(ROOT / "build" / "build_info.json").write_text(json.dumps({"version": VERSION, "build": BUILD_VERSION}))

datas = [(str(ROOT / "build" / "build_info.json"), "ailurus_tone"),
         (str(ROOT / "ailurus_tone" / "web"), "ailurus_tone/web"),
         (str(ROOT / "ailurus_tone" / "help"), "ailurus_tone/help")]
datas += collect_data_files("py_mini_racer")          # icudtl.dat must sit next to the package
binaries = collect_dynamic_libs("py_mini_racer")     # libmini_racer.dylib (V8)
hidden = (collect_submodules("uvicorn")
          + collect_submodules("mcp", filter=lambda m: not m.startswith("mcp.cli")) + ["rtmidi"])
WINDOWS = sys.platform == "win32"
if WINDOWS:
    datas += collect_data_files("webview")           # pywebview's WebView2 loader and .NET glue
    hidden += collect_submodules("webview.platforms.edgechromium") + ["clr"]
    binaries += [(str(ROOT / "build" / "winsparkle" / "WinSparkle.dll"), ".")]   # updater, MIT
else:
    hidden += ["PyObjCTools.AppHelper"]

a = Analysis([str(ROOT / "packaging" / "launch.py")], pathex=[str(ROOT)], binaries=binaries,
             datas=datas, hiddenimports=hidden, excludes=["tkinter", "PIL", "pytest"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Ailurus Tone Editor",
          console=bool(os.environ.get("AILURUS_CONSOLE")), argv_emulation=False, target_arch=None,
          icon=str(ROOT / "assets" / "build" / "icon.ico") if WINDOWS else None,
          version=str(ROOT / "build" / "version_info.txt") if WINDOWS else None)
coll = COLLECT(exe, a.binaries, a.datas, name="Ailurus Tone Editor")
app = None if WINDOWS else BUNDLE(
    coll, name="Ailurus Tone Editor.app", icon=str(ROOT / "assets" / "build" / "AppIcon.icns"),
    bundle_identifier=BUNDLE_ID, version=VERSION,
    info_plist={
        "CFBundleName": "Ailurus Tone Editor",
        "CFBundleDisplayName": "Ailurus Tone Editor",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": BUILD_VERSION,
        "SUFeedURL": FEED_URL,
        "SUPublicEDKey": SPARKLE_PUBLIC_KEY,
        "SUScheduledCheckInterval": 86400,     # once a day
        "CFBundleIconName": "AppIcon",          # layered icon from Assets.car (macOS 26+)
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.music",
        "NSHumanReadableCopyright": "© 2026 David Urban. PolyForm Noncommercial 1.0.0.",
    },
)
