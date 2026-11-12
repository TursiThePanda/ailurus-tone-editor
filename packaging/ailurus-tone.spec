# PyInstaller spec for Ailurus Tone Editor (macOS .app). Build with packaging/build_macos.sh.
import os
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
BUNDLE_ID = "io.github.tursithepanda.ailurus-tone-editor"
# Sparkle update feed = appcast.xml on the latest GitHub release (override for local tests)
FEED_URL = os.environ.get("AILURUS_FEED_URL") or \
    "https://github.com/TursiThePanda/ailurus-tone-editor/releases/latest/download/appcast.xml"
# Every update must be signed with this key's private half (login Keychain, account
# "ailurus-tone", and the repository secret SPARKLE_ED_PRIVATE_KEY).
SPARKLE_PUBLIC_KEY = os.environ.get("AILURUS_SPARKLE_PUBLIC_KEY") or \
    "lpWSxlpiuX5W2Q2NbfJTBK16yng8KVmzy5acmBPL+/M="  # env = throwaway key, local tests only
# Sparkle compares CFBundleVersion: version + commit count, set by build_macos.sh
BUILD_VERSION = os.environ.get("AILURUS_BUILD_VERSION") or VERSION

datas = [(str(ROOT / "ailurus_tone" / "web"), "ailurus_tone/web"),
         (str(ROOT / "ailurus_tone" / "help"), "ailurus_tone/help")]
datas += collect_data_files("py_mini_racer")          # icudtl.dat must sit next to the package
binaries = collect_dynamic_libs("py_mini_racer")     # libmini_racer.dylib (V8)
hidden = (collect_submodules("uvicorn")
          + collect_submodules("mcp", filter=lambda m: not m.startswith("mcp.cli")) + ["rtmidi", "PyObjCTools.AppHelper"])

a = Analysis([str(ROOT / "packaging" / "launch.py")], pathex=[str(ROOT)], binaries=binaries,
             datas=datas, hiddenimports=hidden, excludes=["tkinter", "PIL", "pytest"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Ailurus Tone Editor",
          console=False, argv_emulation=False, target_arch=None)
coll = COLLECT(exe, a.binaries, a.datas, name="Ailurus Tone Editor")
app = BUNDLE(
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
