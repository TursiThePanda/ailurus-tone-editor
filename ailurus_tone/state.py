"""App state shared by the window, the REST API and the MCP tools: one owner of the GX-10."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from .backup import BulkBackup, config_dir, load_config
from .bts import BTSError, Tables, find_bts_root
from .device import GX10, PORT_NAME
from .history import ActivityLog
from .roland import NotConnected, RolandPort

HELP_DIR = Path(__file__).parent / "help"


def bts_running() -> bool:
    """BOSS TONE STUDIO polls the unit and corrupts our reads, so we never run alongside it."""
    try:
        if sys.platform == "win32":
            out = subprocess.run(["tasklist"], capture_output=True, text=True).stdout
            return "BOSS TONE STUDIO" in out.upper()
        return subprocess.run(["pgrep", "-f", "BOSS TONE STUDIO"], capture_output=True).returncode == 0
    except OSError:
        return False


def load_help() -> dict:
    out = {}
    for f in HELP_DIR.glob("*.json"):
        out[f.stem] = json.loads(f.read_text(encoding="utf-8"))
    return out


class State:
    def __init__(self):
        self.version = 0                 # bumped on every change; the UI polls it
        self.error: str | None = None
        self.tables: Tables | None = None
        self.gx: GX10 | None = None
        self.backup: BulkBackup | None = None
        self.help = load_help()
        self.log = ActivityLog(config_dir() / "history")
        try:
            self.tables = Tables(find_bts_root())
            self.gx = GX10(self.tables, on_event=self.bump, log=self.log)
        except BTSError as e:
            self.error = str(e)

    def start_watcher(self):
        threading.Thread(target=self._watch, daemon=True).start()

    def bump(self, *_):
        self.version += 1

    def _watch(self):
        """Connect when the GX-10 appears, drop it when it goes away, refuse while BTS runs."""
        while True:
            self.log.flush_stale()
            if self.gx:
                try:
                    self._watch_once()
                except Exception as e:
                    self.error = f"Connection failed: {e}"
                    self.gx.port.close()
                    self.bump()
            time.sleep(2)

    def _watch_once(self):
        avail = RolandPort.available(PORT_NAME)
        if self.gx.connected and not avail:
            self.gx.port.close()
            self.error = "GX-10 was unplugged"
            self.bump()
        elif not self.gx.connected and avail:
            if bts_running():
                self.error = "Quit BOSS TONE STUDIO first: it polls the GX-10 and corrupts reads"
            else:
                self.gx.connect()
                self.error = None
            self.bump()
        elif not avail:
            self.error = "GX-10 not found. Plug it straight into the computer (not a hub)."

    def need(self) -> GX10:
        if not self.gx or not self.gx.connected or self.gx.temp is None:
            raise NotConnected(self.error or "GX-10 is not connected")
        return self.gx

    def start_backup(self, folder: str | None = None) -> dict:
        if self.backup and self.backup.running:
            return self.backup.status()
        self.backup = BulkBackup(self.need(), folder)
        self.backup.start()
        return self.backup.status()

    def status(self) -> dict:
        connected = bool(self.gx and self.gx.connected and self.gx.temp)
        s = {"version": self.version, "connected": connected, "error": self.error,
             "bts_version": self.tables.bts_version if self.tables else None,
             "backup": self.backup.status() if self.backup else None,
             "backup_dir": load_config()["backup_dir"],
             "history": self.log.recent(60)}
        if connected:
            s["patch"] = self.gx.summary()
        return s
