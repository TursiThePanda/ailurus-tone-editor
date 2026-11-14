"""Automatic updates of the packaged app: Sparkle on macOS, WinSparkle on Windows.

Both read the same feed - appcast.xml on the latest GitHub release - and accept only updates
signed with the EdDSA key whose public half is PUBLIC_KEY (WinSparkle verifies Sparkle's
signatures). The feed has one item per platform: the macOS enclosure is marked
sparkle:os="macos", the Windows one "windows-x64", so neither updater picks the other's file.
Both check once a day and on demand (macOS: app menu; every platform: Settings → About).
When run from source (python -m ailurus_tone) there is no updater.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

FEED_URL = os.environ.get("AILURUS_FEED_URL") or \
    "https://github.com/TursiThePanda/ailurus-tone-editor/releases/latest/download/appcast.xml"
# Private half: David's login Keychain (account "ailurus-tone-editor") and the repository
# secret SPARKLE_ED_PRIVATE_KEY. The env override is for local tests with a throwaway key.
PUBLIC_KEY = os.environ.get("AILURUS_SPARKLE_PUBLIC_KEY") or "SXtYux7aYW1uqXfGmQ53urbMglj07Rl8THr8bc+8Wx4="
CHECK_INTERVAL = 86400                      # once a day

_controller = None                          # macOS: SPUStandardUpdaterController
_winsparkle = None                          # Windows: the loaded WinSparkle.dll
_callbacks = []                             # keep ctypes callbacks alive while the DLL holds them


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
    ws.win_sparkle_set_appcast_url(FEED_URL.encode())
    if not ws.win_sparkle_set_eddsa_public_key(PUBLIC_KEY.encode()):
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


# ---- both ----------------------------------------------------------------------------
def available() -> bool:
    return framework_path() is not None or dll_path() is not None


def start(on_quit=lambda: None):
    """Start the platform's updater once the window shows (pywebview's start() hook)."""
    if framework_path() is not None:
        on_main_thread(_mac_start)
    elif dll_path() is not None:
        _win_start(on_quit)


def check_now():
    """"Check for Updates…": the updater shows its own window with the result."""
    if _controller is not None:
        on_main_thread(lambda: _controller.checkForUpdates_(None))
    elif _winsparkle is not None:
        _winsparkle.win_sparkle_check_update_with_ui()


def stop():
    if _winsparkle is not None:
        _winsparkle.win_sparkle_cleanup()
