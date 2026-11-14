"""Bulk backup of all stored patches of a unit, and the app config (default backup folder).

The unit says what to read: backup_slots() -> [(n, label)], read_slot(n) -> blocks,
backup_layout(), backup_prefix and slot_name(blocks)."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

FORMAT = "ailurus-tone-backup/1"


def config_dir() -> Path:
    if sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / "Ailurus Tone Editor"
    elif sys.platform == "win32":
        d = Path(os.environ.get("APPDATA", Path.home())) / "Ailurus Tone Editor"
    else:
        d = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "ailurus-tone-editor"
    d.mkdir(parents=True, exist_ok=True)
    return d


DEFAULTS = {"backup_dir": str(Path.home() / "Documents" / "Ailurus Tone Editor" / "Backups"),
            "ai_can_save": False}         # MCP gx_save works only when the player turns this on


def load_config() -> dict:
    f = config_dir() / "config.json"
    cfg = dict(DEFAULTS)
    if f.is_file():
        cfg.update(json.loads(f.read_text()))
    return cfg


def save_config(cfg: dict):
    (config_dir() / "config.json").write_text(json.dumps(cfg, indent=2))


class BulkBackup:
    """Runs in a background thread; holds the device lock per patch so live edits keep working."""

    def __init__(self, gx, folder: str | None = None):
        self.gx = gx
        self.folder = Path(folder or load_config()["backup_dir"]).expanduser()
        self.done = 0
        self.slots = gx.backup_slots()
        self.total = len(self.slots)
        self.error: str | None = None
        self.path: str | None = None
        self.running = False
        self.started = 0.0

    def start(self):
        if self.folder == Path(DEFAULTS["backup_dir"]):   # our own default: create it on first use
            self.folder.mkdir(parents=True, exist_ok=True)
        if not self.folder.is_dir():
            raise FileNotFoundError(f"Backup folder does not exist: {self.folder}")
        if not os.access(self.folder, os.W_OK):
            raise PermissionError(f"Backup folder is not writable: {self.folder}")
        self.running, self.started = True, time.time()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            patches = []
            for k, (n, label) in enumerate(self.slots):
                with self.gx.lock:
                    blocks = self.gx.read_slot(n)
                patches.append({"slot": n, "label": label, "name": self.gx.slot_name(blocks), "blocks": blocks})
                self.done = k + 1
            stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            out = self.folder / f"{self.gx.backup_prefix}_all-patches_{stamp}.json"
            out.write_text(json.dumps({
                "format": FORMAT, "device": self.gx.title, "created": datetime.now().isoformat(timespec="seconds"),
                "bts_version": self.gx.t.bts_version, "block_layout": self.gx.backup_layout(),
                "patches": patches,
            }))
            self.path = str(out)
        except Exception as e:
            self.error = str(e)
        finally:
            self.running = False

    def status(self) -> dict:
        return {"running": self.running, "done": self.done, "total": self.total,
                "path": self.path, "error": self.error,
                "seconds": round(time.time() - self.started, 1) if self.started else 0}
