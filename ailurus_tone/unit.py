"""The part of a unit that is the same for every BOSS unit edited over Roland SysEx.

A unit class (see units/) subclasses RolandUnit and fills in what is specific to it: where the
current sound and the stored patches live, how a patch is decoded into blocks and parameters,
and how a parameter is written. The base class owns the connection (editor mode on/off), the
undo/redo history per patch, "edited vs. saved", the diary and the backup interface.

THE SAFETY RULE every unit keeps: live edits go to the unit's current sound (TEMP / edit
buffer) only. A stored patch is written ONLY by save() - the player's Save / Save as in the
window, or the AI when the player has allowed it in Settings (David's decision, 2026-11-14) -
and save() first backs up what it overwrites, refuses slots outside writable_slots() and reads
the slot back to check it.

What the rest of the app reads from a unit (State, REST API, MCP tools, the window):
    kind, title, hint, port_name, readonly, supports_backup, backup_prefix   (class attributes)
    t (its BTS tables: bts_version, root, close()), lock, port, error, connected, temp
    connect(), disconnect(), poll(), reload()
    summary(), describe_fx(fx), patch_list(), parse_slot(text)
    set_param(), set_switch(), record_change(), undo(), redo(), set_chain(), select_patch()
    backup_slots(), read_slot(n), slot_name(blocks), backup_layout()
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .history import ActivityLog
from .locate import BTSProduct
from .roland import RolandPort

EDITOR_MODE = [0x7F, 0, 0, 1]   # 1 = the unit reports its own panel changes as DT1; 0 on leave


@dataclass
class Change:
    """One undoable edit. `block`/`offset` locate it for units whose layout changes with a type
    or variation (the KATANA); the GX-10 finds its parameters by fx number + name."""
    fx: int
    param: str
    old: int
    new: int
    who: str
    block: str = ""
    offset: int = 0
    t: float = field(default_factory=time.time)


class RolandUnit:
    # ---- what a unit class declares ------------------------------------------------
    kind = ""                    # id used in the API, the MCP `device` argument and config.json
    title = ""                   # shown to the player
    aliases: tuple[str, ...] = ()  # more words the MCP `device` argument accepts ('katana')
    hint = ""                    # how to connect it; shown only next to this unit
    bts: BTSProduct | None = None  # which BOSS TONE STUDIO holds its tables (see locate.py)
    port_name = ""               # MIDI port name of the unit
    readonly = False
    supports_backup = True
    backup_prefix = ""

    @classmethod
    def load_tables(cls, bts_folder: str | None):
        """Read this unit's tables from its BTS (`bts_folder` = chosen in Settings, None = search)."""
        raise NotImplementedError

    @classmethod
    def create(cls, bts_folder: str | None, log: ActivityLog | None = None, on_event=None):
        return cls(cls.load_tables(bts_folder), on_event=on_event, log=log)

    # ---- common state ----------------------------------------------------------------
    def __init__(self, tables, on_event=None, log: ActivityLog | None = None):
        self.t = tables
        self.log = log or ActivityLog(None)
        self.port = RolandPort(self.port_name, tables.header, on_unsolicited=self._unsolicited)
        self.on_event = on_event or (lambda kind, data: None)
        self.lock = threading.RLock()
        self.current: int | None = None        # patch number on the unit
        self.temp = None                       # the current sound, as read from the unit
        self.saved = None                      # the stored patch it came from, for "changed vs saved"
        self.names: list[str] = []
        self.history: dict[int, dict] = {}     # patch -> {"undo": [Change], "redo": [Change]}
        self.edited = False
        self.error: str | None = None
        self._selecting: int | None = None     # patch we are switching to; the unit echoes it back

    # ---- connection ----------------------------------------------------------------
    def connect(self):
        with self.lock:
            self.port.open()
            self.port.dt1(EDITOR_MODE, [1], pause=0.2)
            self.names = self.read_names()
            self.reload()

    def disconnect(self):
        with self.lock:
            try:
                self.port.dt1(EDITOR_MODE, [0])
            except Exception:
                pass
            self.port.close()

    @property
    def connected(self) -> bool:
        return self.port.connected

    def poll(self):
        """Called every 2 s while connected. Units that report every change by themselves in
        editor mode need nothing here."""

    def reload(self):
        """Read the patch number, the current sound and the stored patch it came from."""
        with self.lock:
            self.current = self.read_current_number()
            self.temp = self.read_current_sound()
            self.saved = self.read_saved(self.current)
            self.history.setdefault(self.current, {"undo": [], "redo": []})
            self.update_edited()
        self.on_event("patch", {"current": self.current})

    def update_edited(self):
        self.edited = self.saved is not None and self.live_masked(self.temp) != self.live_masked(self.saved)

    # ---- what a unit implements ------------------------------------------------------------
    def read_names(self) -> list[str]:
        raise NotImplementedError

    def read_current_number(self) -> int:
        raise NotImplementedError

    def read_current_sound(self):
        raise NotImplementedError

    def read_saved(self, n: int):
        """The stored patch n, or None when the current sound has no stored patch behind it."""
        raise NotImplementedError

    def live_masked(self, patch):
        """`patch` with values that move by themselves (pedal positions, LEDs) blanked."""
        return patch

    def slot_labels(self) -> list[str]:
        """Panel label of every stored patch, by patch number."""
        raise NotImplementedError

    def apply_change(self, c: Change, value: int, who: str):
        """Write `value` back for undo/redo, without recording a new history entry."""
        raise NotImplementedError

    def log_change(self, who: str, action: str, c: Change, old, new):
        """Diary entry for an undo/redo."""
        raise NotImplementedError

    def _unsolicited(self, addr, data):
        """A DT1 the unit sent by itself (editor mode): a knob, a switch, a patch change."""

    # ---- patch switches ------------------------------------------------------------------------
    def switch_patch(self, n: int, send, who: str):
        """Switch to patch n: `send()` sends the unit's own select command. In editor mode the unit
        then reports the new patch number back, as if the switch had been made on the unit;
        unit_switched_patch() recognises that echo by `_selecting`."""
        with self.lock:
            self._selecting = n
            try:
                send()
                self.reload()
                self._log(who, "patch")
            finally:
                time.sleep(0.2)                    # let a late echo still find _selecting set
                self._selecting = None

    def unit_switched_patch(self, n: int | None):
        """The unit reports (editor mode) that it is on patch n. Not news when it is our own select
        echoed back, or the patch we are already on with nothing edited; a re-select of the same
        patch on the unit with edits does count (the unit threw the edits away)."""
        if n is not None and (n == self._selecting or (n == self.current and not self.edited)):
            return
        time.sleep(0.3)
        self.reload()
        self._log("unit", "patch")

    def _log(self, who: str, action: str, **extra):
        raise NotImplementedError

    # ---- patches -----------------------------------------------------------------------------
    def label(self) -> str:
        labels = self.slot_labels()
        return labels[self.current] if self.current is not None and 0 <= self.current < len(labels) else "?"

    def patch_list(self) -> list[dict]:
        labels = self.slot_labels()
        return [{"slot": i, "label": labels[i], "name": n} for i, n in enumerate(self.names)]

    def parse_slot(self, slot: str) -> int:
        s = slot.upper().replace(" ", "")
        for i, lab in enumerate(self.slot_labels()):
            if lab.upper().replace(" ", "") == s:
                return i
        raise ValueError(f"slot must be one of: {', '.join(self.slot_labels())}")

    @staticmethod
    def name_of(raw: list[int]) -> str:
        """Patch names are 16 ASCII bytes, padded with spaces."""
        return bytes(raw[:16]).decode("ascii", "replace").rstrip()

    # ---- history -----------------------------------------------------------------------------
    def _history(self) -> dict:
        return self.history.setdefault(self.current, {"undo": [], "redo": []})

    def remember(self, c: Change):
        h = self._history()
        h["undo"].append(c)
        h["redo"].clear()

    def history_summary(self) -> dict:
        h = self.history.get(self.current, {"undo": [], "redo": []})
        return {"can_undo": bool(h["undo"]), "can_redo": bool(h["redo"]),
                "changes": [c.__dict__ for c in h["undo"][-50:]][::-1]}

    def undo(self, who: str = "you") -> dict | None:
        return self._step("undo", "redo", who)

    def redo(self, who: str = "you") -> dict | None:
        return self._step("redo", "undo", who)

    def _step(self, src: str, dst: str, who: str) -> dict | None:
        with self.lock:
            h = self.history.get(self.current)
            if not h or not h[src]:
                return None
            c = h[src].pop()
            value, was = (c.old, c.new) if src == "undo" else (c.new, c.old)
            self.apply_change(c, value, who)
            h[dst].append(c)
            self.log_change(who, src, c, was, value)
        self.on_event("change", {"fx": c.fx})
        return c.__dict__

    def set_switch(self, fx: int, on: bool, who: str = "you") -> dict:
        return self.set_param(fx, "OFF/ON", 1 if on else 0, who=who)

    def set_chain(self, order: list[int], who: str = "you", record: bool = True) -> dict:
        raise ValueError(f"The {self.title} cannot reorder its effects.")

    # ---- saving (the only writes to stored patches) ---------------------------------------
    def writable_slots(self) -> list[int]:
        """Patch numbers save() may write. Hidden or internal slots never; a unit narrows this
        further where a slot is not the player's (the KATANA's PANEL)."""
        return [n for n, _ in self.backup_slots()]

    def set_name(self, name: str):
        """Put `name` into the current sound (16 ASCII characters)."""
        raise NotImplementedError

    def store(self, n: int):
        """Send the unit's own WRITE command: the current sound becomes stored patch n. Waits for
        the unit's confirmation."""
        raise NotImplementedError

    def load_into_current(self, blocks):
        """Make the current sound equal to `blocks` (as read_slot() returns them)."""
        raise NotImplementedError

    @staticmethod
    def clean_name(name: str) -> str:
        n = "".join(c if 32 <= ord(c) < 127 else "?" for c in str(name)).strip()[:16]
        if not n:
            raise ValueError("The patch name is empty.")
        return n

    def save(self, n: int, name: str | None = None, who: str = "you", backup_dir: str | None = None) -> dict:
        """Store the current sound as patch n (optionally renamed). What n held before is written
        to <backup_dir>/Overwritten first; afterwards slot n is read back and compared."""
        labels = self.slot_labels()
        if n not in self.writable_slots():
            raise ValueError(f"{labels[n] if 0 <= n < len(labels) else n} cannot be saved to. "
                             f"Choose one of: {', '.join(labels[i] for i in self.writable_slots())}")
        with self.lock:
            if self.temp is None:
                raise ValueError(f"The {self.title} is not connected.")
            old = self.read_slot(n)
            backup = self._backup_overwritten(n, old, backup_dir)
            if name is not None and self.clean_name(name) != self.patch_name():
                self.set_name(self.clean_name(name))
            self._selecting = n                    # the unit may report the switch to n
            try:
                self.store(n)
                time.sleep(0.5)
                stored = self.read_saved(n)
                if stored is None or self.live_masked(stored) != self.live_masked(self.temp):
                    time.sleep(1.5)                # reads right after a write can be garbage
                    stored = self.read_saved(n)
                ok = stored is not None and self.live_masked(stored) == self.live_masked(self.temp)
                saved_name = self.patch_name()
                if 0 <= n < len(self.names):
                    self.names[n] = saved_name
                self.reload()
            finally:
                time.sleep(0.2)
                self._selecting = None
            self.log.add(who=who, action="save", patch=labels[n], name=saved_name, device=self.title,
                         backup=backup)
        self.on_event("patch", {"current": self.current})
        if not ok:
            raise RuntimeError(f"Saved to {labels[n]}, but reading it back differs from the sound that was "
                               f"sent. The old patch is in {backup}.")
        return {"slot": labels[n], "name": saved_name, "backup": backup}

    def _backup_overwritten(self, n: int, blocks, backup_dir: str | None) -> str:
        from .backup import FORMAT, load_config
        folder = Path(backup_dir or load_config()["backup_dir"]).expanduser() / "Overwritten"
        folder.mkdir(parents=True, exist_ok=True)
        label = self.slot_labels()[n]
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        out = folder / f"{self.backup_prefix}_{label.replace(':', '')}_{stamp}.json"
        k = 2
        while out.exists():                        # never replace an earlier backup
            out = folder / f"{self.backup_prefix}_{label.replace(':', '')}_{stamp}_{k}.json"
            k += 1
        out.write_text(json.dumps({
            "format": FORMAT, "device": self.title, "created": datetime.now().isoformat(timespec="seconds"),
            "bts_version": self.t.bts_version, "block_layout": self.backup_layout(),
            "patches": [{"slot": n, "label": label, "name": self.slot_name(blocks), "blocks": blocks}],
        }))
        return str(out)

    def put_back(self, path: str, who: str = "you", backup_dir: str | None = None) -> dict:
        """Write a patch from an "Overwritten" backup back into its slot (via the current sound,
        which is replaced). What the slot holds now is backed up again, so this can be undone too."""
        data = json.loads(Path(path).read_text())
        if data.get("device") != self.title or len(data.get("patches", [])) != 1:
            raise ValueError(f"{Path(path).name} is not a single {self.title} patch backup.")
        p = data["patches"][0]
        want = self.live_masked(self._as_read(p["blocks"]))
        with self.lock:
            for _attempt in range(2):              # right after a save the unit can miss a write
                self.load_into_current(p["blocks"])
                for _ in range(2):                 # ... or answer a read too early
                    self.temp = self.read_current_sound()
                    if self.live_masked(self.temp) == want:
                        return self.save(int(p["slot"]), who=who, backup_dir=backup_dir)
                    time.sleep(1.5)
            self.update_edited()
            raise RuntimeError("The unit did not take the backed-up patch exactly (differs in: "
                               f"{', '.join(self._differing(self.live_masked(self.temp), want))}); "
                               "nothing was saved.")

    @staticmethod
    def _differing(a, b) -> list[str]:
        if isinstance(a, dict):
            return [k for k in b if a.get(k) != b[k]]
        return [str(i) for i, (x, y) in enumerate(zip(a, b, strict=False)) if x != y]

    def _as_read(self, blocks):
        """A backup's blocks in the shape read_current_sound() returns (units may read fewer blocks)."""
        return blocks

    # ---- backup (see backup.BulkBackup) ---------------------------------------------------------
    def backup_slots(self) -> list[tuple[int, str]]:
        """(patch number, label) of every stored patch a backup reads. Hidden or internal
        slots must not be listed."""
        return list(enumerate(self.slot_labels()))
