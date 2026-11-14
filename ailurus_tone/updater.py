"""Automatic updates of the packaged app: Sparkle on macOS, WinSparkle on Windows; on Linux
(AppImage) only a notice that a newer release exists - the user downloads it himself.

Both read the same feed - appcast.xml on the latest GitHub release - and accept only updates
signed with the EdDSA key whose public half is PUBLIC_KEY (WinSparkle verifies Sparkle's
signatures). The feed has one item per platform: the macOS enclosure is marked
sparkle:os="macos", the Windows one "windows-x64", so neither updater picks the other's file.
Both check once a day and on demand (macOS: app menu; every platform: Settings → About).
When run from source (python -m ailurus_tone) there is no updater.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.request
from pathlib import Path

DEFAULT_FEED_URL = "https://github.com/TursiThePanda/ailurus-tone-editor/releases/latest/download/appcast.xml"
# Private half: David's login Keychain (account "ailurus-tone-editor") and the repository
# secret SPARKLE_ED_PRIVATE_KEY.
DEFAULT_PUBLIC_KEY = "SXtYux7aYW1uqXfGmQ53urbMglj07Rl8THr8bc+8Wx4="
# A BUILD can be pointed at a local feed and a throwaway key (update tests) with these
# variables; the packaging spec bakes the result into the app (Info.plist / build_info.json),
# so the variables mean nothing to an installed app.
FEED_URL = os.environ.get("AILURUS_FEED_URL") or DEFAULT_FEED_URL
PUBLIC_KEY = os.environ.get("AILURUS_SPARKLE_PUBLIC_KEY") or DEFAULT_PUBLIC_KEY
CHECK_INTERVAL = 86400                      # once a day
LATEST_API = "https://api.github.com/repos/TursiThePanda/ailurus-tone-editor/releases/latest"

_controller = None                          # macOS: SPUStandardUpdaterController
_winsparkle = None                          # Windows: the loaded WinSparkle.dll
_callbacks = []                             # keep ctypes callbacks alive while the DLL holds them
_linux = {"started": False, "notice": None, "checked": None}   # Linux: last check and its result


# ---- macOS: Sparkle ------------------------------------------------------------------
def framework_path():
    """Sparkle.framework inside the running .app, or None when not packaged."""
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return None
    path = Path(sys.executable).resolve().parent.parent / "Frameworks" / "Sparkle.framework"
    return path if path.is_dir() else None


def _mac_start():
    """Create Sparkle's standard updater. Runs on the main thread once Cocoa runs."""
    global _controller
    path = framework_path()
    if path is None or _controller is not None:
        return
    import objc
    found = {}
    objc.loadBundle("Sparkle", found, bundle_path=str(path))
    controller_class = found["SPUStandardUpdaterController"]
    _controller = controller_class.alloc().initWithStartingUpdater_updaterDelegate_userDriverDelegate_(
        True, None, None)


def on_main_thread(fn):
    """Run fn on the Cocoa main thread (pywebview calls menu actions, start() hooks and API
    requests from other threads; Sparkle's UI must live on the main one)."""
    from PyObjCTools import AppHelper
    AppHelper.callAfter(fn)


# ---- Windows: WinSparkle -------------------------------------------------------------
def dll_path():
    """WinSparkle.dll bundled with the packaged app, or None when not packaged."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return None
    path = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "WinSparkle.dll"
    return path if path.is_file() else None


def _win_start(on_quit):
    """Load WinSparkle and start its daily check. `on_quit` closes the app: WinSparkle calls it
    (from its own thread) after it has started the downloaded installer."""
    global _winsparkle
    path = dll_path()
    if path is None or _winsparkle is not None:
        return
    import ctypes

    from .about import build_info
    ws = ctypes.CDLL(str(path))
    info = build_info()
    feed = info.get("feed_url") or DEFAULT_FEED_URL      # baked in at build time
    key = info.get("public_key") or DEFAULT_PUBLIC_KEY
    ws.win_sparkle_set_appcast_url(feed.encode())
    if not ws.win_sparkle_set_eddsa_public_key(key.encode()):
        return                              # never run an updater that cannot verify
    ws.win_sparkle_set_app_details.argtypes = [ctypes.c_wchar_p] * 3
    ws.win_sparkle_set_app_details("David Urban", "Ailurus Tone Editor", info["version"])
    ws.win_sparkle_set_app_build_version.argtypes = [ctypes.c_wchar_p]
    ws.win_sparkle_set_app_build_version(str(info["build"]))   # compared with sparkle:version
    can_quit = ctypes.CFUNCTYPE(ctypes.c_int)(lambda: 1)
    quit_now = ctypes.CFUNCTYPE(None)(lambda: on_quit())
    _callbacks.extend([can_quit, quit_now])
    ws.win_sparkle_set_can_shutdown_callback(can_quit)
    ws.win_sparkle_set_shutdown_request_callback(quit_now)
    ws.win_sparkle_set_automatic_check_for_updates(1)
    ws.win_sparkle_set_update_check_interval(CHECK_INTERVAL)
    ws.win_sparkle_init()
    _winsparkle = ws


# ---- Linux: a notice only -------------------------------------------------------------
def linux_packaged() -> bool:
    """The AppImage (its runtime sets APPIMAGE); not when run from source."""
    return sys.platform.startswith("linux") and getattr(sys, "frozen", False)


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", text)[:3])


def check_latest(current: str, fetch=None) -> dict:
    """Ask GitHub for the latest release. {"latest", "url", "newer"}; raises OSError offline."""
    def get():
        req = urllib.request.Request(LATEST_API, headers={"Accept": "application/vnd.github+json",
                                                          "User-Agent": "ailurus-tone-editor"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    rel = (fetch or get)()
    latest = str(rel.get("tag_name", "")).lstrip("v")
    newer = bool(latest) and _version(latest) > _version(current)
    return {"latest": latest, "url": rel.get("html_url") or "", "newer": newer}


def _linux_check() -> dict:
    from .about import build_info
    r = check_latest(build_info()["version"])
    _linux["checked"] = time.time()
    _linux["notice"] = r if r["newer"] else None
    return r


def _linux_loop():
    while True:
        try:
            _linux_check()
        except (OSError, ValueError):
            pass                            # offline: try again tomorrow
        time.sleep(CHECK_INTERVAL)


def notice() -> dict | None:
    """Linux: {"latest", "url"} once a newer release is known, for the banner."""
    return _linux["notice"]


# ---- both ----------------------------------------------------------------------------
def available() -> bool:
    return framework_path() is not None or dll_path() is not None or linux_packaged()


def start(on_quit=lambda: None):
    """Start the platform's updater once the window shows (pywebview's start() hook)."""
    if framework_path() is not None:
        on_main_thread(_mac_start)
    elif dll_path() is not None:
        _win_start(on_quit)
    elif linux_packaged() and not _linux["started"]:
        _linux["started"] = True
        threading.Thread(target=_linux_loop, daemon=True).start()


def check_now() -> dict | None:
    """"Check for Updates…": Sparkle/WinSparkle show their own window with the result;
    on Linux the result is returned for the page to show."""
    if _controller is not None:
        on_main_thread(lambda: _controller.checkForUpdates_(None))
    elif _winsparkle is not None:
        _winsparkle.win_sparkle_check_update_with_ui()
    elif linux_packaged():
        return _linux_check()
    return None


def stop():
    if _winsparkle is not None:
        _winsparkle.win_sparkle_cleanup()
