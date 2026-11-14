"""Entry point for the packaged app (PyInstaller runs this file)."""
import sys

if sys.stdout is None or sys.stderr is None:
    # A windowed Windows build has no console: sys.stdout/stderr are None and the first
    # library that writes or asks isatty() fails (uvicorn's log setup does). Send them to a
    # log file in the app's config folder instead - also the place to look when it misbehaves.
    from ailurus_tone.backup import config_dir

    log = open(config_dir() / "app.log", "w", encoding="utf-8", buffering=1)  # noqa: SIM115
    sys.stdout = sys.stdout or log
    sys.stderr = sys.stderr or log

from ailurus_tone.__main__ import main  # noqa: E402

main()
