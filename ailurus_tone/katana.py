"""BOSS KATANA Gen 3 device profile (Katana-50/100/Artist/Head Gen 3): READ-ONLY for now.

Facts proven on a KATANA-50 EX Gen 3 on 2026-11-14 (read-only probe, no DT1 sent):
- MIDI port 'KATANA3' (needs the "KATANA3 Driver", not the older KATANA driver), SysEx header
  F0 41 10 01 05 07 (model id from BTS product_setting.js).
- Addresses in address_map.js are 4 raw 7-bit bytes: PATCH 0x20000000 = the sound being played
  (edit buffer), PATCH(n) 0x20n00000... = stored memories, SETUP 0x00000000 = patch number
  (0 = PANEL, 1..8 = A:CH1..B:CH4), PATCH(10) = PREVIEW (never touched).
- The panel has fixed sections; BOOSTER, MOD, FX, DELAY (+DELAY2) and REVERB each have three
  variations (GREEN/RED/YELLOW). PATCH COLOR says which one is active; MOD = FX(1..3), FX = FX(4..6),
  DELAY2 = DELAY(4..6). FX_DETAIL(n) holds the parameters of every FX type for FX(n).

Nothing is written to the amp yet: edits, patch switching and backups raise a clear error.
"""
from __future__ import annotations

import threading

from .bts import BTSError
from .history import ActivityLog
from .katana_bts import KatanaTables
from .roland import RolandPort, add7, from7

PORT_NAME = "KATANA3"
EDIT_BUFFER = [0x20, 0, 0, 0]
SETUP = [0, 0, 0, 0]
NUM_PATCHES = 9                       # PANEL + A:CH1..A:CH4 + B:CH1..B:CH4
VARIATIONS = ["GREEN", "RED", "YELLOW"]
VARIATION_CSS = ["green", "red", "yellow"]
LIVE_WORDS = ("PEDAL POS",)           # pedal positions follow the physical pedal; never an edit
READ_ONLY = "Editing the KATANA is not available yet: this version only reads it."
POLL_BLOCKS = ("SW", "COLOR", "AMP", "PEDALFX_COM", "EQ_EACH(1)", "EQ_EACH(2)", "NS", "SENDRETURN", "SOLO_COM")


def patch_base(n: int) -> list[int]:
    """Stored memory n (0 = PANEL) -> PATCH(n+1) base address."""
    i = n + 1
    return [0x20 + i // 8, (i % 8) << 4, 0, 0]


def decode(block: list[int], p: dict) -> int:
    o, kind, n = p["offset"], p["kind"], p["n"]
    raw_b = block[o:o + n]
    if kind == "nibbles":
        raw = 0
        for x in raw_b:
            raw = (raw << 4) | (x & 15)
    elif kind == "byte7":
        raw = (raw_b[0] << 7) | raw_b[1]
    else:
        raw = raw_b[0]
    return raw - p["ofs"]


# Logical sections in the Katana's default signal order. Each entry:
# (title, css colour or None = variation colour, switch (block, offset) or None,
#  variation (COLOR offset) or None, block chooser)
SECTIONS = [
    ("PEDAL FX", "orange", ("PEDALFX_COM", 1), None, "pedalfx"),
    ("BOOSTER", None, ("SW", 0), 0, "booster"),
    ("MOD", None, ("SW", 1), 1, "mod"),
    ("AMP", "brown", None, None, "amp"),
    ("FX", None, ("SW", 2), 2, "fx"),
    ("DELAY", None, ("SW", 3), 3, "delay"),
    ("DELAY 2", None, ("SW", 4), 3, "delay2"),
    ("REVERB", None, ("SW", 5), 4, "reverb"),
    ("EQ", "white", ("EQ_EACH(1)", 1), None, "eq1"),
    ("EQ 2", "white", ("EQ_EACH(2)", 1), None, "eq2"),
    ("NOISE SUPPRESSOR", "gray_blank", ("NS", 0), None, "ns"),
    ("SEND/RETURN", "gray_blank", ("SENDRETURN", 0), None, "sr"),
    ("SOLO", "pink", ("SOLO_COM", 0), None, "solo"),
    ("CONTOUR", "cyan", None, None, "contour"),
]


class Katana:
    kind = "katana3"
    title = "KATANA Gen 3"
    port_name = PORT_NAME
    readonly = True
    supports_backup = False

    def __init__(self, tables: KatanaTables, on_event=None, log: ActivityLog | None = None):
        self.t = tables
        self.log = log or ActivityLog(None)
        self.port = RolandPort(PORT_NAME, tables.header)
        self.on_event = on_event or (lambda kind, data: None)
        self.lock = threading.RLock()
        self.current: int | None = None
        self.temp: dict[str, list[int]] | None = None
        self.saved: dict[str, list[int]] | None = None
        self.names: list[str] = []
        self.edited = False
        self.error: str | None = None
        labels = next((r for r in tables.resources if r and r[0] == "PANEL" and len(r) == NUM_PATCHES), None)
        if not labels:
            raise BTSError("patch labels (PANEL, A: CH1, ...) not found in KATANA resource.js")
        self.labels = [x.replace(": ", "") for x in labels]       # PANEL, ACH1 -> shown as 'A:CH1'
        self.labels = [x if x == "PANEL" else f"{x[0]}:{x[1:]}" for x in self.labels]
        self.read_blocks = [b for b in tables.blocks
                            if not b.startswith("ASSIGN") and "READONLY" not in b and b != "COM"] + ["COM"]

    # ---- connection ----------------------------------------------------------
    def connect(self):
        with self.lock:
            self.port.open()
            self.names = self.read_names()
            self.reload()

    def disconnect(self):
        with self.lock:
            self.port.close()

    @property
    def connected(self) -> bool:
        return self.port.connected

    # ---- reads ---------------------------------------------------------------
    def _rq(self, base, block: str) -> list[int]:
        off, _, size = self.t.blocks[block]
        return self.port.rq1(add7(base, from7(off)), size)

    def read_names(self) -> list[str]:
        return [bytes(self._rq(patch_base(n), "COM")[:16]).decode("ascii", "replace").rstrip()
                for n in range(NUM_PATCHES)]

    def read_patch(self, base) -> dict[str, list[int]]:
        return {b: self._rq(base, b) for b in self.read_blocks}

    def read_current_number(self) -> int:
        b = self.port.rq1(SETUP, 2)          # INTEGER2x4: two nibbles
        return ((b[0] & 15) << 4) | (b[1] & 15)

    def reload(self):
        with self.lock:
            self.current = self.read_current_number()
            self.temp = self.read_patch(EDIT_BUFFER)
            self.saved = self.read_patch(patch_base(self.current)) if 0 <= self.current < NUM_PATCHES else None
            self._update_edited()
        self.on_event("patch", {"current": self.current})

    def poll(self):
        """Called by the watcher every couple of seconds: the amp does not report panel changes by
        itself without editor mode, so re-read the patch number and the panel-facing blocks."""
        with self.lock:
            n = self.read_current_number()
            if n != self.current:
                self.reload()
                self._log("unit", "patch")
                return
            changed = False
            for b in POLL_BLOCKS:
                if b not in self.temp:
                    continue
                new = self._rq(EDIT_BUFFER, b)
                if new != self.temp[b]:
                    self.temp[b] = new
                    changed = True
            if changed:
                # a colour or type may have moved to other blocks: read the whole sound again
                self.temp = self.read_patch(EDIT_BUFFER)
                self._update_edited()
        if changed:
            self.on_event("change", {"from": "unit"})

    def _update_edited(self):
        self.edited = self.saved is not None and self._masked(self.temp) != self._masked(self.saved)

    def _masked(self, patch) -> dict:
        out = {k: list(v) for k, v in patch.items()}
        for b in out:
            _, table, _ = self.t.blocks[b]
            for r in self.t.tables[table]:
                if any(w in r["id"].replace("_", " ") for w in LIVE_WORDS):
                    out[b][r["offset"]:r["offset"] + r["n"]] = [0] * r["n"]
        return out

    # ---- sections ------------------------------------------------------------
    def _value(self, patch, block: str, offset: int) -> int:
        _, table, _ = self.t.blocks[block]
        p = next(r for r in self.t.tables[table] if r["offset"] == offset)
        return decode(patch[block], p)

    def _variation(self, patch, color_off: int | None) -> int:
        if color_off is None:
            return 0
        return max(0, min(2, self._value(patch, "COLOR", color_off)))

    def _parts(self, i: int, patch) -> list[tuple[str, str | None, set[str]]]:
        """[(block, type prefix filter, param ids to skip)] that make up section i in `patch`."""
        _, _, sw, var, chooser = SECTIONS[i]
        c = self._variation(patch, var)
        if chooser == "booster":
            return [(f"BOOSTER({c + 1})", None, set())]
        if chooser in ("mod", "fx"):
            n = c + 1 + (3 if chooser == "fx" else 0)
            t = self._value(patch, f"FX({n})", 0)
            g = self.t.fx_groups[t] if 0 <= t < len(self.t.fx_groups) else None
            return [(f"FX({n})", None, set()), (f"FX_DETAIL({n})", g, set())]
        if chooser == "amp":
            return [("AMP", None, set())]
        if chooser in ("delay", "delay2"):
            return [(f"DELAY({c + 1 + (3 if chooser == 'delay2' else 0)})", None, set())]
        if chooser == "reverb":
            return [(f"REVERB({c + 1})", None, set())]
        if chooser == "pedalfx":
            t = self._value(patch, "PEDALFX_COM", 2)
            g = self.t.pedal_groups[t] if 0 <= t < len(self.t.pedal_groups) else None
            return [("PEDALFX_COM", None, {"PRMID_PATCH_PEDALFX_COM_SW"}), ("PEDALFX", g, set())]
        if chooser in ("eq1", "eq2"):
            k = 1 if chooser == "eq1" else 2
            t = self._value(patch, f"EQ_EACH({k})", 2)
            return [(f"EQ_EACH({k})", None, {"PRMID_PATCH_EQ_EACH_SW"}),
                    (f"EQ_GE10({k})" if t == 1 else f"EQ_PEQ({k})", None, set())]
        if chooser == "ns":
            return [("NS", None, {"PRMID_PATCH_NS_SW"})]
        if chooser == "sr":
            return [("SENDRETURN", None, {"PRMID_PATCH_SENDRETURN_SW"})]
        if chooser == "solo":
            return [("SOLO_COM", None, {"PRMID_PATCH_SOLO_COM_SW"}), ("SOLO_EQ", None, set()),
                    ("SOLO_DELAY", None, set())]
        if chooser == "contour":
            k = self._value(patch, "CONTOUR_COM", 0)
            return [("CONTOUR_COM", None, set())] + ([(f"CONTOUR({k})", None, set())] if 1 <= k <= 3 else [])
        raise KeyError(chooser)

    def _type_text(self, i: int, patch) -> str:
        """The section's current type as BTS names it (e.g. 'T-SCREAM'), '' when it has none."""
        for block, _, _ in self._parts(i, patch):
            for p in self.t.params(block):
                if p["id"].endswith("_TYPE") and block.split("(")[0] in (
                        "BOOSTER", "FX", "DELAY", "REVERB", "EQ_EACH", "PEDALFX_COM"):
                    return self.t.display(p, decode(patch[block], p))
        return ""

    def _on(self, i: int, patch) -> bool:
        sw = SECTIONS[i][2]
        if sw:
            return bool(self._value(patch, *sw))
        if SECTIONS[i][4] == "contour":
            return self._value(patch, "CONTOUR_COM", 0) > 0
        return True

    def _color(self, i: int, patch) -> str:
        css, _, var = SECTIONS[i][1], SECTIONS[i][2], SECTIONS[i][3]
        return css or VARIATION_CSS[self._variation(patch, var)]

    def describe_fx(self, fx: int) -> dict:
        with self.lock:
            i = fx - 1
            if not 0 <= i < len(SECTIONS):
                raise KeyError(f"no block {fx}; the KATANA has blocks 1..{len(SECTIONS)}")
            title, _, sw, var, _ = SECTIONS[i]
            params, seen = [], set()

            def add(name, value, display, lo, hi, options, saved, live=False):
                base, k = name, 2
                while name in seen:
                    name, k = f"{base} {k}", k + 1
                seen.add(name)
                params.append({"name": name, "value": value, "display": display, "min": lo, "max": hi,
                               "options": options, "saved": saved, "live": live,
                               "changed": saved is not None and saved != value and not live})

            same_layout = self.saved is not None and self._parts(i, self.saved) == self._parts(i, self.temp)
            if sw:
                p = self._prm(*sw)
                v = decode(self.temp[sw[0]], p)
                add("OFF/ON", v, "ON" if v else "OFF", 0, 1, ["OFF", "ON"],
                    decode(self.saved[sw[0]], p) if self.saved else None)
            if var is not None:
                p = self._prm("COLOR", var)
                v = decode(self.temp["COLOR"], p)
                add("VARIATION", v, VARIATIONS[v] if 0 <= v < 3 else str(v), 0, 2, VARIATIONS,
                    decode(self.saved["COLOR"], p) if self.saved else None)
            for block, prefix, skip in self._parts(i, self.temp):
                for p in self.t.params(block, prefix):
                    if p["id"] in skip or p["kind"] == "text":
                        continue
                    v = decode(self.temp[block], p)
                    sv = decode(self.saved[block], p) if same_layout else None
                    live = any(w in p["id"].replace("_", " ") for w in LIVE_WORDS)
                    add(p["name"], v, self.t.display(p, v), p["min"], p["max"], self.t.options(p), sv, live)
            return {"fx": fx, "type": title, "sub": self._type_text(i, self.temp), "color": self._color(i, self.temp),
                    "on": self._on(i, self.temp), "params": params, "readonly": True}

    def _prm(self, block: str, offset: int) -> dict:
        _, table, _ = self.t.blocks[block]
        return next(r for r in self.t.tables[table] if r["offset"] == offset)

    # ---- summaries -----------------------------------------------------------
    def label(self) -> str:
        return self.labels[self.current] if self.current is not None and 0 <= self.current < NUM_PATCHES else "?"

    def patch_name(self) -> str:
        return bytes(self.temp["COM"][:16]).decode("ascii", "replace").rstrip()

    def patch_list(self) -> list[dict]:
        return [{"slot": i, "label": self.labels[i], "name": n} for i, n in enumerate(self.names)]

    def parse_slot(self, slot: str) -> int:
        s = slot.upper().replace(" ", "")
        for i, lab in enumerate(self.labels):
            if lab.replace(" ", "") == s:
                return i
        raise ValueError(f"slot must be one of: {', '.join(self.labels)}")

    def summary(self) -> dict:
        with self.lock:
            chain = [{"fx": i + 1, "type": s[0], "sub": self._type_text(i, self.temp),
                      "color": self._color(i, self.temp), "on": self._on(i, self.temp)}
                     for i, s in enumerate(SECTIONS)]
            return {"current": self.current, "label": self.label(), "name": self.patch_name(),
                    "edited": self.edited, "chain": chain, "can_undo": False, "can_redo": False,
                    "straight": False, "readonly": True, "changes": []}

    def _log(self, who: str, action: str):
        self.log.add(who=who, action=action, patch=self.label(), name=self.patch_name(), device=self.title)

    # ---- edits: not yet --------------------------------------------------------
    def set_param(self, *_a, **_k):
        raise ValueError(READ_ONLY)

    def set_switch(self, *_a, **_k):
        raise ValueError(READ_ONLY)

    def set_chain(self, *_a, **_k):
        raise ValueError("The KATANA has a fixed chain; it cannot be reordered here.")

    def record_change(self, *_a, **_k):
        raise ValueError(READ_ONLY)

    def select_patch(self, *_a, **_k):
        raise ValueError("Switching KATANA patches from the app is not available yet: use the amp's buttons.")

    def undo(self, who: str = "you"):
        return None

    def redo(self, who: str = "you"):
        return None
