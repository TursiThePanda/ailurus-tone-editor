"""Ailurus Tone Editor entry point: one process owns the MIDI port and serves the window, REST and MCP.

    python -m ailurus_tone            own app window (system webview); on Linux the UI opens in the browser
    python -m ailurus_tone --server   no window: MCP + UI at http://127.0.0.1:8710/
    python -m ailurus_tone --demo F   no GX-10 needed: one patch from backup file F, nothing is sent
    --port N                  listen on another port (default 8710)

MCP endpoint (Streamable HTTP): http://127.0.0.1:8710/mcp
Draft 1: edits go to the unit's TEMP (current sound) only; no user slot is ever written.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from . import api, mcp_tools, updater
from .state import State

HOST, PORT = "127.0.0.1", 8710


WEB = Path(__file__).parent / "web"
ICON = WEB / "AppIcon.icns" if (WEB / "AppIcon.icns").is_file() else WEB / "icon.png"  # icns = Apple's render


def set_dock_icon():
    """Show our icon in the Dock instead of Python's while running from source (macOS only).
    A packaged app will carry icon.icns in its bundle instead."""
    if sys.platform != "darwin" or getattr(sys, "frozen", False) or not ICON.is_file():
        return
    try:
        from AppKit import NSApplication, NSImage
        NSApplication.sharedApplication().setApplicationIconImage_(NSImage.alloc().initWithContentsOfFile_(str(ICON)))
    except ImportError:
        pass


class WindowAPI:
    """Native functions the page can call through window.pywebview.api."""

    def pick_folder(self, start: str = ""):
        import webview
        r = webview.windows[0].create_file_dialog(webview.FileDialog.FOLDER,
                                                  directory=start or str(Path.home()))
        return r[0] if r else None


def already_running(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((HOST, port)) == 0


def open_browser(url: str):
    """The user's default browser. A packaged app points LD_LIBRARY_PATH at its own libraries;
    the browser must get the system's (PyInstaller keeps the original in *_ORIG)."""
    env = dict(os.environ)
    for var in ("LD_LIBRARY_PATH", "LD_PRELOAD"):
        if f"{var}_ORIG" in env:
            env[var] = env.pop(f"{var}_ORIG")
        elif getattr(sys, "frozen", False):
            env.pop(var, None)
    try:
        subprocess.Popen(["xdg-open", url], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        import webbrowser
        webbrowser.open(url)


def run_in_browser(server: uvicorn.Server, state: State, port: int):
    """Linux: no app window - serve, open the UI in the browser, run until Quit or Ctrl+C."""
    url = f"http://{HOST}:{port}/"
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(200):
        if server.started or not t.is_alive():
            break
        time.sleep(0.05)
    if not server.started:
        sys.exit(f"Ailurus Tone Editor: cannot listen on {HOST}:{port}.")
    state.quit = lambda: setattr(server, "should_exit", True)
    print(f"Ailurus Tone Editor runs at {url} (MCP: {url}mcp). Quit from Settings → About, or Ctrl+C.")
    updater.start()
    open_browser(url)
    try:
        while t.is_alive():
            t.join(0.5)
    except KeyboardInterrupt:
        server.should_exit = True
        t.join(5)


def build(state: State, port: int = PORT):
    mcp = MCPServer(name="ailurus-tone", title="Ailurus Tone Editor (BOSS GX-10, KATANA Gen 3)",
                    instructions=mcp_tools.INSTRUCTIONS)
    mcp_tools.register(mcp, state)
    api.register(mcp, state)
    return mcp.streamable_http_app(
        transport_security=TransportSecuritySettings(
            allowed_hosts=[f"{HOST}:{port}", f"localhost:{port}"],
            allowed_origins=[f"http://{HOST}:{port}", f"http://localhost:{port}"],
        ),
        host=HOST,
    )


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    port = int(argv[argv.index("--port") + 1]) if "--port" in argv else PORT
    browser = sys.platform.startswith("linux") and "--server" not in argv
    if browser and already_running(port):      # started again from the menu: show the running one
        open_browser(f"http://{HOST}:{port}/")
        return
    state = State(only=["gx10"] if "--demo" in argv else None)
    if "--demo" in argv:
        from .demo import load_demo
        state.log.folder = None   # demo edits never reach the real diary
        state.log.events.clear()
        if not state.gx:
            sys.exit("Demo mode needs BOSS TONE STUDIO for GX-10 installed (for the parameter tables).")
        load_demo(state.gx, argv[argv.index("--demo") + 1])
        state.gx.error = None
    else:
        state.start_watcher()
    state.port = port
    server = uvicorn.Server(uvicorn.Config(build(state, port), host=HOST, port=port, log_level="warning"))
    try:
        if "--server" in argv:
            server.run()
            return
        if browser:
            run_in_browser(server, state, port)
            return
        import webview
        threading.Thread(target=server.run, daemon=True).start()
        for _ in range(100):  # wait until the server listens
            if server.started:
                break
            time.sleep(0.05)
        if not server.started:  # e.g. another Ailurus Tone Editor already holds the port
            sys.exit(f"Ailurus Tone Editor: cannot listen on {HOST}:{port} - is another copy already running?")
        webview.create_window("Ailurus Tone Editor", f"http://{HOST}:{port}/", js_api=WindowAPI(),
                              width=1440, height=900, min_size=(1000, 660))
        set_dock_icon()
        menu = []
        if updater.available() and sys.platform == "darwin":   # Windows has no app menu: Settings → About
            from webview.menu import Menu, MenuAction
            menu = [Menu("__app__", [MenuAction("Check for Updates…", updater.check_now)])]

        def close_window():               # WinSparkle started the installer: get out of its way
            for w in list(webview.windows):
                w.destroy()

        # blocks on the main thread until the window closes; the hook runs once the window is up
        webview.start(lambda: updater.start(on_quit=close_window), menu=menu)
        server.should_exit = True
        updater.stop()
    finally:
        state.log.flush()
        state.close()


if __name__ == "__main__":
    main()
