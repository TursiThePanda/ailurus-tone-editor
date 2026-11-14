"""Demo mode: run the whole app without a GX-10, from a single-patch backup JSON.

    python -m ailurus_tone --demo path/to/backup.json

The fake port only records what would be sent; nothing touches real hardware.
"""
from __future__ import annotations

import json
from pathlib import Path

from .roland import NotConnected
from .units.gx10 import GX10, VISIBLE_USER


class DemoPort:
    connected = True

    def __init__(self):
        self.sent = []

    def dt1(self, addr, data, pause: float = 0.0):
        self.sent.append((list(addr), list(data)))

    def rq1(self, addr, size, timeout=0.0):
        raise NotConnected("Demo mode: only the loaded patch is available")

    def open(self):
        pass

    def close(self):
        pass


def load_demo(gx: GX10, backup_file: str):
    d = json.loads(Path(backup_file).read_text())
    blocks = d["blocks"]
    slot = int(d.get("slot", 1)) - 1          # backups store the 1-based internal slot
    gx.port = DemoPort()
    gx.current = min(max(slot, 0), VISIBLE_USER - 1)
    gx.temp = [list(b) for b in blocks]
    gx.saved = [list(b) for b in blocks]
    gx.names = [f"DEMO {i + 1}" for i in range(VISIBLE_USER)]
    gx.names[gx.current] = d.get("name", "DEMO")
    gx.history.setdefault(gx.current, {"undo": [], "redo": []})
    gx.edited = False
