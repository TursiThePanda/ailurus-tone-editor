# PyInstaller spec for Ailurus Tone Editor (macOS .app). Build with packaging/build_macos.sh.
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
BUNDLE_ID = "io.github.tursithepanda.ailurus-tone-editor"

datas = [(str(ROOT / "ailurus_tone" / "web"), "ailurus_tone/web"),
         (str(ROOT / "ailurus_tone" / "help"), "ailurus_tone/help")]
datas += collect_data_files("py_mini_racer")          # icudtl.dat must sit next to the package
binaries = collect_dynamic_libs("py_mini_racer")     # libmini_racer.dylib (V8)
hidden = (collect_submodules("uvicorn")
          + collect_submodules("mcp", filter=lambda m: not m.startswith("mcp.cli")) + ["rtmidi"])

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
        "CFBundleIconName": "AppIcon",          # layered icon from Assets.car (macOS 26+)
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.music",
        "NSHumanReadableCopyright": "© 2026 David Urban. PolyForm Noncommercial 1.0.0.",
    },
)
