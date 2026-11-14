"""App state shared by the window, the REST API and the MCP tools: one owner of every supported unit.

Several units may be plugged in at once (e.g. a GX-10 and a KATANA). Each has its own device
object and MIDI port; one of them is ACTIVE: that is the one the window shows and the tools edit
unless a tool names another one. When the active unit goes away and another is connected, the
other one becomes active by itself.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from .backup import BulkBackup, config_dir, load_config
from .bts import BTSError, Tables, find_bts_root
from .device import GX10
from .history import ActivityLog
from .katana import Katana
from .katana_bts import KatanaTables, find_katana_root
from .roland import NotConnected, RolandPort

HELP_DIR = Path(__file__).parent / "help"

# id -> (title, how to build it); order = preference for the first active unit
PROFILES = {
    "gx10": ("GX-10", lambda log, bump: GX10(Tables(find_bts_root()), on_event=bump, log=log),
             "GX-10 not found. Plug it straight into the computer (not a hub)."),
    "katana3": ("KATANA Gen 3", lambda log, bump: Katana(KatanaTables(find_katana_root()), on_event=bump, log=log),
                "KATANA not found. Plug it in over USB and install the KATANA3 Driver."),
}


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
    def __init__(self, only: list[str] | None = None):
        self.version = 0                 # bumped on every change; the UI polls it
        self.devices: dict = {}          # id -> GX10 | Katana (only units whose BTS is installed)
        self.missing: dict[str, str] = {}  # id -> why it is not available (BTS not installed ...)
        self.active: str | None = None
        self.backup: BulkBackup | None = None
        self.help = load_help()
        self.log = ActivityLog(config_dir() / "history")
        for did, (_, make, _) in PROFILES.items():
            if only and did not in only:
                continue
            try:
                self.devices[did] = make(self.log, self.bump)
            except BTSError as e:
                self.missing[did] = str(e)
        self.active = next(iter(self.devices), None)

    # ---- compatibility: the GX-10 object, used by demo mode -------------------
    @property
    def gx(self) -> GX10 | None:
        return self.devices.get("gx10")

    @property
    def error(self) -> str | None:
        d = self.devices.get(self.active) if self.active else None
        if len(self.devices) > 1 and not any(self._ready(x) for x in self.devices.values()):
            return "No unit connected. " + " ".join(x.error for x in self.devices.values() if x.error)
        if d:
            return d.error
        return next(iter(self.missing.values()), None) or "No supported unit is set up."

    @error.setter
    def error(self, msg: str | None):
        d = self.devices.get(self.active) if self.active else None
        if d:
            d.error = msg

    def start_watcher(self):
        threading.Thread(target=self._watch, daemon=True).start()

    def bump(self, *_):
        self.version += 1

    def _watch(self):
        """Connect each unit when it appears, drop it when it goes away, refuse while BTS runs."""
        while True:
            self.log.flush_stale()
            for d in list(self.devices.values()):
                try:
                    self._watch_once(d)
                except Exception as e:
                    d.error = f"Connection failed: {e}"
                    d.port.close()
                    self.bump()
            self._auto_select()
            time.sleep(2)

    def _watch_once(self, d):
        avail = RolandPort.available(d.port_name)
        if d.connected and not avail:
            d.port.close()
            d.error = f"{d.title} was unplugged"
            self.bump()
        elif not d.connected and avail:
            if bts_running():
                d.error = f"Quit BOSS TONE STUDIO first: it polls the {d.title} and corrupts reads"
            else:
                d.connect()
                d.error = None
            self.bump()
        elif not avail:
            d.error = PROFILES[d.kind][2]
        elif d.connected and d.temp is not None:
            d.poll()

    def _ready(self, d) -> bool:
        return bool(d and d.connected and d.temp is not None)

    def _auto_select(self):
        """Keep the active unit if it is there; otherwise move to one that is."""
        if self._ready(self.devices.get(self.active)):
            return
        for did, d in self.devices.items():
            if self._ready(d):
                if did != self.active:
                    self.active = did
                    self.bump()
                return

    def select_device(self, did: str) -> dict:
        did = self.resolve(did)
        self.active = did
        self.bump()
        return self.status()

    def resolve(self, did: str | None) -> str:
        """'katana', 'KATANA Gen 3', 'gx-10' ... -> device id."""
        if did is None:
            if not self.active:
                raise NotConnected(self.error)
            return self.active
        key = did.lower().replace("-", "").replace(" ", "")
        for i, d in self.devices.items():
            if key in (i, d.title.lower().replace("-", "").replace(" ", "")) or i.startswith(key):
                return i
        known = ", ".join(d.title for d in self.devices.values()) or "none"
        raise ValueError(f"unknown unit '{did}' (set up: {known})")

    def need(self, did: str | None = None):
        d = self.devices.get(self.resolve(did))
        if not self._ready(d):
            raise NotConnected(d.error or f"{d.title} is not connected")
        return d

    def start_backup(self, folder: str | None = None) -> dict:
        if self.backup and self.backup.running:
            return self.backup.status()
        d = self.need()
        if not d.supports_backup:
            raise ValueError(f"Backing up the {d.title} is not available yet.")
        self.backup = BulkBackup(d, folder)
        self.backup.start()
        return self.backup.status()

    def devices_status(self) -> list[dict]:
        out = [{"id": i, "title": d.title, "connected": self._ready(d), "active": i == self.active,
                "readonly": d.readonly, "can_backup": d.supports_backup,
                "error": None if self._ready(d) else d.error}
               for i, d in self.devices.items()]
        out += [{"id": i, "title": PROFILES[i][0], "connected": False, "active": False,
                 "readonly": True, "error": msg, "missing": True} for i, msg in self.missing.items()]
        return out

    def status(self) -> dict:
        d = self.devices.get(self.active) if self.active else None
        connected = self._ready(d)
        any_bts = next(iter(self.devices.values()), None)
        s = {"version": self.version, "connected": connected, "error": None if connected else self.error,
             "device": self.active, "device_title": d.title if d else None,
             "can_backup": bool(d and d.supports_backup),
             "devices": self.devices_status(),
             "bts_version": d.t.bts_version if d else (any_bts.t.bts_version if any_bts else None),
             "backup": self.backup.status() if self.backup else None,
             "backup_dir": load_config()["backup_dir"],
             "history": self.log.recent(60)}
        if connected:
            s["patch"] = d.summary()
        return s

    def close(self):
        for d in self.devices.values():
            if d.connected:
                d.disconnect()
            try:
                d.t.close()
            except Exception:
                pass
