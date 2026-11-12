"""Automatic updates through Sparkle (macOS app only).

The packaged app carries Sparkle.framework in Contents/Frameworks. Sparkle reads the feed
(SUFeedURL in Info.plist: appcast.xml on the latest GitHub release), checks once a day and
accepts only updates signed with the EdDSA key whose public half is SUPublicEDKey.
When run from source (python -m ailurus_tone) there is no framework and no updater.
"""

import sys
from pathlib import Path

_controller = None


def framework_path():
    """Sparkle.framework inside the running .app, or None when not packaged."""
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return None
    path = Path(sys.executable).resolve().parent.parent / "Frameworks" / "Sparkle.framework"
    return path if path.is_dir() else None


def available():
    return framework_path() is not None


def start():
    """Create Sparkle's standard updater. Must run on the main thread once Cocoa runs."""
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


def check_now():
    """The "Check for Updates…" menu item: Sparkle shows its own window with the result."""
    if _controller is not None:
        _controller.checkForUpdates_(None)


def on_main_thread(fn):
    """Run fn on the Cocoa main thread (pywebview calls menu actions and start() hooks from
    other threads; Sparkle's UI must live on the main one)."""
    from PyObjCTools import AppHelper
    AppHelper.callAfter(fn)
