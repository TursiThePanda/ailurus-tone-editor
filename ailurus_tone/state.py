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

from .backup import BulkBackup, config_dir, load_config, save_config
from .history import ActivityLog
from .locate import BTSError, data_dir, download_installer, from_installer, newest_download
from .roland import NotConnected, RolandPort
from .unit import RolandUnit
from .units import BY_KIND

HELP_DIR = Path(__file__).parent / "help"

# Every supported unit comes from units.UNITS. A unit's connection hint is shown only next to
# that unit (unit menu tooltip, a tool that names it); the main messages stay generic so they
# hold for any number of supported units.
PROFILES = BY_KIND
NO_UNIT = "No unit connected. Plug in a supported unit over USB and quit BOSS TONE STUDIO."
NO_PROFILE = ("BOSS TONE STUDIO was not found for any supported unit. Install it (it does not need to run), "
              "or choose its folder in Settings.")


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
        self.devices: dict[str, RolandUnit] = {}  # id -> unit (only units whose BTS is installed)
        self.missing: dict[str, str] = {}  # id -> why it is not available (BTS not installed ...)
        self.active: str | None = None
        self.backup: BulkBackup | None = None
        self.help = load_help()
        self.log = ActivityLog(config_dir() / "history")
        self.lock = threading.RLock()     # the watcher vs. replacing a unit after a BTS folder change
        self.only = only
        chosen = load_config().get("bts_paths", {})
        for did in PROFILES:
            if only and did not in only:
                continue
            try:
                self.devices[did] = self._make(did, chosen.get(did))
            except BTSError as e:
                self.missing[did] = str(e)
        self.active = next(iter(self.devices), None)

    def _make(self, did: str, bts: str | None):
        return PROFILES[did].create(bts, log=self.log, on_event=self.bump)

    # ---- compatibility: the GX-10 object, used by demo mode -------------------
    @property
    def gx(self) -> RolandUnit | None:
        return self.devices.get("gx10")

    @property
    def error(self) -> str | None:
        """Why nothing can be edited. A unit's own error is a real problem (BTS running, a failed
        connection); a unit that is simply not plugged in has none, so the message stays generic."""
        if not self.devices:
            return NO_PROFILE
        d = self.devices.get(self.active) if self.active else None
        if self._ready(d):
            return None
        if any(self._ready(x) for x in self.devices.values()):
            return None
        problems = [x.error for x in self.devices.values() if x.error]
        return problems[0] if problems else NO_UNIT

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
            with self.lock:
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
            d.error = None
            self.bump()
        elif not d.connected and avail:
            if bts_running():
                d.error = "Quit BOSS TONE STUDIO first: it polls the unit and corrupts reads"
            else:
                d.connect()
                d.error = None
            self.bump()
        elif not avail:
            d.error = None
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
        """'katana', 'KATANA Gen 3', 'gx-10' ... -> device id (the unit's kind, title or aliases)."""
        if did is None:
            if not self.active:
                raise NotConnected(self.error)
            return self.active
        def norm(x: str) -> str:
            return x.lower().replace("-", "").replace(" ", "")

        key = norm(did)
        for i, d in self.devices.items():
            if key in {norm(n) for n in (i, d.title, *d.aliases)} or i.startswith(key):
                return i
        known = ", ".join(d.title for d in self.devices.values()) or "none"
        raise ValueError(f"unknown unit '{did}' (set up: {known})")

    def need(self, did: str | None = None):
        d = self.devices.get(self.resolve(did))
        if not self._ready(d):
            raise NotConnected(d.error or f"{d.title} is not connected. {self.hint(d)}")
        return d

    @staticmethod
    def hint(d) -> str:
        return d.hint

    # ---- where each unit's BOSS TONE STUDIO is --------------------------------
    def set_bts_path(self, did: str, path: str | None) -> dict:
        """Use the BTS in `path` for unit `did` (None or "" = look in the usual places again).
        The tables are loaded first; only if that works is the unit replaced and the choice saved."""
        if did not in PROFILES or (self.only and did not in self.only):
            raise ValueError(f"unknown unit '{did}'")
        path = (path or "").strip() or None
        new = self._make(did, path)                     # BTSError -> nothing changes
        with self.lock:
            old = self.devices.get(did)
            if old:
                if old.connected:
                    old.disconnect()
                try:
                    old.t.close()
                except Exception:
                    pass
            self.devices = {i: (new if i == did else d) for i, d in self._ordered(did, new)}
            self.missing.pop(did, None)
            if not self.active:
                self.active = did
            cfg = load_config()
            paths = cfg.setdefault("bts_paths", {})
            if path:
                paths[did] = path
            else:
                paths.pop(did, None)
            save_config(cfg)
            self.bump()
        return {"bts": self.bts_status()}

    def install_bts(self, did: str, filename: str | None = None, data: bytes | None = None,
                    agreed: bool = False) -> dict:
        """Linux: unpack the unit's BTS Windows installer into our data folder and use it from there.
        Either the user gave us the installer (.exe or .zip: filename + data), or he accepted
        Roland's licence and we download Roland's zip ourselves."""
        if did not in PROFILES or (self.only and did not in self.only):
            raise ValueError(f"unknown unit '{did}'")
        product = PROFILES[did].bts
        up = data_dir() / ".upload"
        if data is not None:
            up.mkdir(parents=True, exist_ok=True)
            f = up / (Path(filename or "").name or "installer.exe")
            f.write_bytes(data)
            return self._unpack_and_use(did, f)
        if not agreed:
            raise ValueError("Accept Roland's software licence agreement first.")
        newest = newest_download(product)
        if newest and newest[1] != product.download:
            try:   # a newer BTS than we know: use it if its tables still read, else the known one
                return self._unpack_and_use(did, download_installer(product, up, newest[1]))
            except BTSError:
                pass
        return self._unpack_and_use(did, download_installer(product, up))

    def _unpack_and_use(self, did: str, installer: Path) -> dict:
        product = PROFILES[did].bts
        try:
            root = from_installer(installer, product)
        finally:
            installer.unlink(missing_ok=True)
        return self.set_bts_path(did, str(data_dir() / product.app)) | {"root": str(root)}

    def _ordered(self, did, new):
        """Keep PROFILES order, so the unit menu does not reshuffle."""
        for i in PROFILES:
            if i == did:
                yield i, new
            elif i in self.devices:
                yield i, self.devices[i]

    def bts_status(self) -> list[dict]:
        chosen = load_config().get("bts_paths", {})
        out = []
        for did, unit in PROFILES.items():
            if self.only and did not in self.only:
                continue
            d = self.devices.get(did)
            out.append({"id": did, "title": unit.title, "chosen": chosen.get(did, ""),
                        "found": str(d.t.root) if d else None,
                        "version": d.t.bts_version if d else None,
                        "error": self.missing.get(did),
                        "installer": sys.platform.startswith("linux"),   # no BTS for Linux: unpack it
                        "download": bool(unit.bts.download), "licence_page": unit.bts.licence_page})
        return out

    def start_backup(self, folder: str | None = None) -> dict:
        if self.backup and self.backup.running:
            return self.backup.status()
        d = self.need()
        if not d.supports_backup:
            raise ValueError(f"Backing up the {d.title} is not available yet.")
        self.backup = BulkBackup(d, folder)
        self.backup.start()
        return self.backup.status()

    # ---- saving ---------------------------------------------------------------------------
    def save_patch(self, slot: int, name: str | None = None, who: str = "you", device: str | None = None) -> dict:
        d = self.need(device)
        if d.readonly:
            raise ValueError(f"Saving the {d.title} is not available.")
        if who == "ai" and not load_config().get("ai_can_save"):
            raise PermissionError("Saving patches is off for the AI. The player can allow it in "
                                  "Settings → General → \"Let the AI save patches\".")
        r = d.save(int(slot), name=name, who=who)
        self.bump()
        return r

    def overwritten_dir(self) -> Path:
        return Path(load_config()["backup_dir"]).expanduser() / "Overwritten"

    def overwritten(self) -> list[dict]:
        """Patches save() replaced on the active unit, newest first (for "Put back")."""
        d = self.need()
        out = []
        folder = self.overwritten_dir()
        for f in sorted(folder.glob(f"{d.backup_prefix}_*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
            try:
                j = json.loads(f.read_text())
                p = j["patches"][0]
            except (ValueError, KeyError, IndexError, OSError):
                continue
            if j.get("device") == d.title:
                out.append({"file": f.name, "label": p.get("label"), "name": p.get("name"),
                            "created": j.get("created")})
        return out[:50]

    def put_back(self, file: str, who: str = "you") -> dict:
        d = self.need()
        f = (self.overwritten_dir() / Path(file).name)   # a name from overwritten(), never a path
        if not f.is_file():
            raise FileNotFoundError(f"No such backup: {file}")
        r = d.put_back(str(f), who=who)
        self.bump()
        return r

    def devices_status(self) -> list[dict]:
        out = [{"id": i, "title": d.title, "connected": self._ready(d), "active": i == self.active,
                "readonly": d.readonly, "can_backup": d.supports_backup,
                "error": None if self._ready(d) else d.error, "hint": self.hint(d)}
               for i, d in self.devices.items()]
        out += [{"id": i, "title": PROFILES[i].title, "connected": False, "active": False,
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
             "ai_can_save": bool(load_config().get("ai_can_save")),
             "bts": self.bts_status(),
             "history": self.log.recent(60)}
        if connected:
            s["patch"] = d.summary()
            labels = d.slot_labels()
            s["patch"]["can_save"] = not d.readonly and d.current in d.writable_slots()
            s["writable"] = [{"slot": n, "label": labels[n], "name": d.names[n] if n < len(d.names) else ""}
                             for n in d.writable_slots()] if not d.readonly else []
        return s

    def close(self):
        for d in self.devices.values():
            if d.connected:
                d.disconnect()
            try:
                d.t.close()
            except Exception:
                pass
