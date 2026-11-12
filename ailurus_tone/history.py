"""Activity log: everything that happened to the sound, by whom, readable by the AI.

Unlike the undo stack (per patch, in memory), this is an append-only diary kept on disk
as one JSON object per line, one file per day: <config>/history/YYYY-MM-DD.jsonl.

Event fields: seq, t (unix time), iso, who ("you" | "ai" | "unit"), action
("set" | "undo" | "redo" | "patch"), patch (slot label), name (patch name) and, for
parameter changes, fx, type, param, old, new, display.

A knob turned on the unit sends a burst of messages. Bursts on the same parameter within
MERGE_WINDOW seconds are merged into one event (first old value, last new value), so the
log says "DRIVE 40 -> 65", not 25 single steps.
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

MERGE_WINDOW = 2.0
KEEP_IN_MEMORY = 2000


class ActivityLog:
    def __init__(self, folder: Path | None):
        self.folder = folder
        if folder:
            folder.mkdir(parents=True, exist_ok=True)
        self.events: deque[dict] = deque(maxlen=KEEP_IN_MEMORY)
        self.seq = 0
        self.lock = threading.Lock()
        self._open: dict | None = None    # last event, still mergeable, not yet on disk
        self._load_recent()

    def _load_recent(self):
        """Pick up where the last run stopped, so the AI still sees what happened before a restart."""
        if not self.folder:
            return
        for f in sorted(self.folder.glob("*.jsonl"))[-2:]:
            for line in f.read_text(encoding="utf-8").splitlines():
                try:
                    self.events.append(json.loads(line))
                except ValueError:
                    continue    # a half-written last line after a crash
        if self.events:
            self.seq = max(e.get("seq", 0) for e in self.events)

    def add(self, merge_key: tuple | None = None, **ev) -> dict:
        now = time.time()
        with self.lock:
            o = self._open
            if (merge_key and o and o.get("_key") == merge_key and now - o["t"] < MERGE_WINDOW):
                o.update(new=ev.get("new"), display=ev.get("display"), t=now)
                if o["new"] == o["old"]:   # turned back where it started: nothing happened
                    self.events.remove(o)
                    self._open = None
                return o
            self._flush()
            self.seq += 1
            e = {"seq": self.seq, "t": now, **ev}
            if merge_key:
                e["_key"] = merge_key
            self.events.append(e)
            self._open = e
            return e

    def _flush(self):
        """Write the open event to today's file. Called with the lock held."""
        e, self._open = self._open, None
        if not e or not self.folder:
            return
        line = {k: v for k, v in e.items() if not k.startswith("_")}
        line["iso"] = datetime.fromtimestamp(e["t"]).isoformat(timespec="seconds")
        day = datetime.fromtimestamp(e["t"]).strftime("%Y-%m-%d")
        with open(self.folder / f"{day}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

    def flush_stale(self):
        """Write the open event once nothing can merge into it any more."""
        with self.lock:
            if self._open and time.time() - self._open["t"] >= MERGE_WINDOW:
                self._flush()

    def flush(self):
        with self.lock:
            self._flush()

    def since(self, seq: int = 0, limit: int = 100) -> list[dict]:
        """Events with seq > `seq`, oldest first, at most `limit` (the newest ones)."""
        with self.lock:
            out = [{k: v for k, v in e.items() if not k.startswith("_")}
                   for e in self.events if e["seq"] > seq]
        return out[-limit:]

    def recent(self, limit: int = 50) -> list[dict]:
        return self.since(0, limit)[::-1]
