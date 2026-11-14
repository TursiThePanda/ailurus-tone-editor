"""What the app is: version, build, links, licence and the libraries it is built with.

The macOS app also shows this in the standard About panel (packaging/Credits.rtf); the
window's Settings → About shows it on every platform, Windows included, which has no app menu.
"""
from __future__ import annotations

import json
import sys
from importlib import metadata
from pathlib import Path

REPO = "https://github.com/TursiThePanda/ailurus-tone-editor"
BUILD_INFO = Path(__file__).parent / "build_info.json"   # written by the packaging spec


def build_info() -> dict:
    """{'version', 'build'} of a packaged app; from the installed package when run from source."""
    try:
        return json.loads(BUILD_INFO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    try:
        v = metadata.version("ailurus-tone-editor")
    except metadata.PackageNotFoundError:
        v = "?"
    return {"version": v, "build": "source"}


# (name, url, licence, platforms); platforms None = everywhere
LIBRARIES = [
    ("pywebview", "https://pywebview.flowrl.com", "BSD-3-Clause", None),
    ("MCP Python SDK", "https://github.com/modelcontextprotocol/python-sdk", "MIT", None),
    ("mini-racer with V8", "https://github.com/bpcreech/PyMiniRacer", "ISC, V8: BSD-3-Clause", None),
    ("python-rtmidi", "https://github.com/SpotlightKid/python-rtmidi", "MIT", None),
    ("Uvicorn", "https://www.uvicorn.org", "BSD-3-Clause", None),
    ("Starlette", "https://www.starlette.io", "BSD-3-Clause", None),
    ("Sparkle", "https://sparkle-project.org", "MIT", ("darwin",)),
    ("PyObjC", "https://pyobjc.readthedocs.io", "MIT", ("darwin",)),
    ("WinSparkle", "https://winsparkle.org", "MIT", ("win32",)),
    ("pythonnet", "https://pythonnet.github.io", "MIT", ("win32",)),
    ("Python", "https://www.python.org", "PSF", None),
    ("PyInstaller", "https://pyinstaller.org", "GPL-2.0 with bootloader exception", None),
]


def about(port: int, can_update: bool) -> dict:
    info = build_info()
    return {
        **info,
        "platform": {"darwin": "macOS", "win32": "Windows"}.get(sys.platform, sys.platform),
        "mcp_url": f"http://127.0.0.1:{port}/mcp",
        "can_update": can_update,
        "links": [{"title": "GitHub", "url": REPO},
                  {"title": "Release notes", "url": f"{REPO}/releases"},
                  {"title": "Report a problem", "url": f"{REPO}/issues"}],
        "licence": {"title": "PolyForm Noncommercial 1.0.0",
                    "url": "https://polyformproject.org/licenses/noncommercial/1.0.0/"},
        "notice": ("Not affiliated with or endorsed by Roland Corporation or BOSS. BOSS, GX-10, KATANA "
                   "and BOSS TONE STUDIO are trademarks of Roland Corporation. The app reads its tables "
                   "from your own BOSS TONE STUDIO installation."),
        "libraries": [{"name": n, "url": u, "licence": lic} for n, u, lic, only in LIBRARIES
                      if not only or sys.platform in only],
    }
