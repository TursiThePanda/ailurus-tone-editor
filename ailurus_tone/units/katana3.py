"""BOSS KATANA Gen 3 device profile (Katana-50/100/Artist/Head Gen 3).

Facts proven on a KATANA-50 EX Gen 3 on 2026-11-14:
- MIDI port 'KATANA3' (needs the "KATANA3 Driver", not the older KATANA driver), SysEx header
  F0 41 10 01 05 07 (model id from BTS product_setting.js).
- Addresses in address_map.js are 4 raw 7-bit bytes: PATCH 0x20000000 = the sound being played
  (edit buffer; what BTS edits), PATCH(n) 0x20n00000... = stored memories, SETUP 0x00000000 =
  patch number (0 = PANEL, 1..8 = A:CH1..B:CH4), PATCH(10) = PREVIEW (never touched).
- Commands (BTS address_const.js): 7F 00 00 01 editor mode (1 on connect, 0 on leave),
  7F 00 01 00 PATCH_SELECT [00, n], 7F 00 01 04 PATCH_WRITE (stores the edit buffer; NEVER sent here).
- The panel has fixed sections; BOOSTER, MOD, FX, DELAY (+DELAY2) and REVERB each have three
  variations (GREEN/RED/YELLOW). PATCH COLOR says which one is active; MOD = FX(1..3), FX = FX(4..6),
  DELAY2 = DELAY(4..6). FX_DETAIL(n) holds the parameters of every FX type for FX(n).
- Signal order = BTS chain_config.js: CHAIN (0-8) x pedal FX position x send/return position,
  with the two EQs placed around the amp by their own positions.

Same rule as the GX-10: edits go to the edit buffer only (heard at once, not stored); nothing
writes a memory. Switching patch discards the edits, as on the amp itself.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from ..locate import BTSError
from ..roland import add7, from7
from ..rolandbts import decode as decode_raw
from ..unit import Change, RolandUnit
from .katana3_bts import KATANA3_BTS, KatanaTables, encode, find_katana_root

PORT_NAME = "KATANA3"
EDIT_BUFFER = [0x20, 0, 0, 0]
SETUP = [0, 0, 0, 0]
PATCH_SELECT = [0x7F, 0, 1, 0]
PATCH_WRITE = [0x7F, 0, 1, 4]          # only listened for (the player stored on the amp)
NUM_PATCHES = 9                       # PANEL + A:CH1..A:CH4 + B:CH1..B:CH4
VARIATIONS = ["GREEN", "RED", "YELLOW"]
VARIATION_CSS = ["green", "red", "yellow"]
LIVE_WORDS = ("PEDAL POS",)           # pedal positions follow the physical pedal; never an edit
POLL_BLOCKS = ("SW", "COLOR", "AMP", "OTHER", "PEDALFX_COM", "EQ_EACH(1)", "EQ_EACH(2)", "NS",
               "SENDRETURN", "SOLO_COM", "CONTOUR_COM")
CHAIN_SHORT = {"pdl": "PEDAL", "bst": "BST", "mod": "MOD", "amp": "AMP", "fx": "FX", "dly1": "DLY1",
               "dly2": "DLY2", "rev": "REV", "sr": "S/R", "eq": "EQ", "eq2": "EQ2"}


def patch_base(n: int) -> list[int]:
    """Stored memory n (0 = PANEL) -> PATCH(n+1) base address."""
    i = n + 1
    return [0x20 + i // 8, (i % 8) << 4, 0, 0]


def decode(block: list[int], p: dict) -> int:
    o = p["offset"]
    return decode_raw(block[o:o + p["n"]], p["kind"], p["ofs"])


# Panel sections. (title, css colour or None = variation colour, switch (block, offset) or None,
#  variation (COLOR offset) or None, block chooser, chain id in BTS chain_config.js or None)
SECTIONS = [
    ("PEDAL FX", "orange", ("PEDALFX_COM", 1), None, "pedalfx", "pdl"),
    ("BOOSTER", None, ("SW", 0), 0, "booster", "bst"),
    ("MOD", None, ("SW", 1), 1, "mod", "mod"),
    ("AMP", "brown", None, None, "amp", "amp"),
    ("FX", None, ("SW", 2), 2, "fx", "fx"),
    ("DELAY", None, ("SW", 3), 3, "delay", "dly1"),
    ("DELAY 2", None, ("SW", 4), 3, "delay2", "dly2"),
    ("REVERB", None, ("SW", 5), 4, "reverb", "rev"),
    ("EQ", "white", ("EQ_EACH(1)", 1), None, "eq1", "eq"),
    ("EQ 2", "white", ("EQ_EACH(2)", 1), None, "eq2", "eq2"),
    ("NOISE SUPPRESSOR", "gray_blank", ("NS", 0), None, "ns", None),
    ("SEND/RETURN", "gray_blank", ("SENDRETURN", 0), None, "sr", "sr"),
    ("SOLO", "pink", ("SOLO_COM", 0), None, "solo", None),
    ("CONTOUR", "cyan", None, None, "contour", None),
]
CHAIN_ID = {s[5]: i for i, s in enumerate(SECTIONS) if s[5]}


@dataclass
class Entry:
    """One parameter as shown in a section: its name there and where it lives in the patch."""
    name: str
    block: str
    p: dict
    options: list[str] | None
    live: bool = False


class Katana(RolandUnit):
    kind = "katana3"
    title = "KATANA Gen 3"
    aliases = ("katana", "katana gen3", "katana3")
    hint = "Plug it in over USB; it needs Roland's KATANA3 Driver."
    bts = KATANA3_BTS
    port_name = PORT_NAME
    backup_prefix = "KATANA-Gen3"

    @classmethod
    def load_tables(cls, bts_folder: str | None) -> KatanaTables:
        return KatanaTables(find_katana_root(bts_folder))

    def __init__(self, tables: KatanaTables, on_event=None, log=None):
        super().__init__(tables, on_event=on_event, log=log)
        self._selecting: int | None = None     # patch we asked for; the amp echoes it back
        labels = next((r for r in tables.resources if r and r[0] == "PANEL" and len(r) == NUM_PATCHES), None)
        if not labels:
            raise BTSError("patch labels (PANEL, A: CH1, ...) not found in KATANA resource.js")
        self.labels = [x.replace(" ", "") for x in labels]          # 'A: CH1' -> 'A:CH1'
        self.read_blocks = [b for b in tables.blocks
                            if not b.startswith("ASSIGN") and "READONLY" not in b and b != "COM"] + ["COM"]
        self._block_at = sorted((from7(off), b) for b, (off, _, _) in tables.blocks.items())

    # ---- reads ---------------------------------------------------------------
    def _rq(self, base, block: str) -> list[int]:
        off, _, size = self.t.blocks[block]
        return self.port.rq1(add7(base, from7(off)), size)

    def read_names(self) -> list[str]:
        return [self.name_of(self._rq(patch_base(n), "COM")) for n in range(NUM_PATCHES)]

    def read_patch(self, base) -> dict[str, list[int]]:
        return {b: self._rq(base, b) for b in self.read_blocks}

    def read_current_number(self) -> int:
        b = self.port.rq1(SETUP, 2)          # INTEGER2x4: two nibbles
        return ((b[0] & 15) << 4) | (b[1] & 15)

    def read_current_sound(self) -> dict[str, list[int]]:
        return self.read_patch(EDIT_BUFFER)

    def read_saved(self, n: int):
        return self.read_patch(patch_base(n)) if 0 <= n < NUM_PATCHES else None

    def poll(self):
        """Safety net next to editor mode: re-read the patch number and the panel-facing blocks."""
        with self.lock:
            n = self.read_current_number()
            if n != self.current:
                self.reload()
                self._log("unit", "patch")
                return
            changed = False
            for b in POLL_BLOCKS:
                if b in self.temp:
                    new = self._rq(EDIT_BUFFER, b)
                    if new != self.temp[b]:
                        self._apply_unit(b, new)
                        changed = True
        if changed:
            self.on_event("change", {"from": "unit"})

    def live_masked(self, patch) -> dict:
        out = {k: list(v) for k, v in patch.items()}
        for b in out:
            _, table, _ = self.t.blocks[b]
            for r in self.t.tables[table]:
                if any(w in r["id"].replace("_", " ") for w in LIVE_WORDS):
                    out[b][r["offset"]:r["offset"] + r["n"]] = [0] * r["n"]
        return out

    # ---- sections ------------------------------------------------------------
    def _prm(self, block: str, offset: int) -> dict:
        _, table, _ = self.t.blocks[block]
        return next(r for r in self.t.tables[table] if r["offset"] == offset)

    def _value(self, patch, block: str, offset: int) -> int:
        return decode(patch[block], self._prm(block, offset))

    def _variation(self, patch, color_off: int | None) -> int:
        if color_off is None:
            return 0
        return max(0, min(2, self._value(patch, "COLOR", color_off)))

    def _parts(self, i: int, patch) -> list[tuple[str, str | None, set[str]]]:
        """[(block, type prefix filter, param ids to skip)] that make up section i in `patch`."""
        chooser, var = SECTIONS[i][4], SECTIONS[i][3]
        c = self._variation(patch, var)
        if chooser == "booster":
            return [(f"BOOSTER({c + 1})", None, set())]
        if chooser in ("mod", "fx"):
            n = c + 1 + (3 if chooser == "fx" else 0)
            t = self._value(patch, f"FX({n})", 0)
            g = self.t.fx_groups[t] if 0 <= t < len(self.t.fx_groups) else None
            return [(f"FX({n})", None, set()), (f"FX_DETAIL({n})", g, set())]
        if chooser == "amp":
            return [("AMP", None, set()), ("OTHER", None, {"PRMID_PATCH_OTHER_MASTER_KEY"})]
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

    def _entries(self, i: int, patch) -> list[Entry]:
        """Every parameter of section i, in display order, with unique names."""
        _, _, sw, var, _, _ = SECTIONS[i]
        out: list[Entry] = []
        seen: set[str] = set()

        def add(name, block, p, options, live=False):
            base, k = name, 2
            while name in seen:
                name, k = f"{base} {k}", k + 1
            seen.add(name)
            out.append(Entry(name, block, p, options, live))

        if sw:
            add("OFF/ON", sw[0], self._prm(*sw), ["OFF", "ON"])
        if var is not None:
            add("VARIATION", "COLOR", self._prm("COLOR", var), VARIATIONS)
        for block, prefix, skip in self._parts(i, patch):
            for p in self.t.params(block, prefix):
                if p["id"] in skip or p["kind"] == "text":
                    continue
                opts = self._chain_texts(patch) if p["id"] == "PRMID_PATCH_OTHER_CHAIN" else self.t.options(p)
                add(p["name"], block, p, opts, any(w in p["id"].replace("_", " ") for w in LIVE_WORDS))
        return out

    def _display(self, e: Entry, v: int) -> str:
        if e.options:
            k = v - e.p["min"]
            if 0 <= k < len(e.options):
                return e.options[k]
        return self.t.display(e.p, v)

    # ---- signal order ----------------------------------------------------------
    def _flow(self, patch, chain: int | None = None) -> list[str]:
        """BTS chain ids in signal order, e.g. ['pdl', 'bst', 'mod', 'eq', 'eq2', 'amp', ...]."""
        pdl = self._value(patch, "PEDALFX_COM", 0)
        sr = self._value(patch, "SENDRETURN", 1)
        ch = self._value(patch, "OTHER", 0) if chain is None else chain
        base = next((x["list"] for x in self.t.chain_base if x["pdlVal"] == pdl and x["srVal"] == sr), None)
        if not base or not 0 <= ch < len(base):
            return [s[5] for s in SECTIONS if s[5]]
        flow = base[ch]
        eq = next((x["list"] for x in self.t.chain_eq if x["eqPos1"] == self._value(patch, "EQ_EACH(1)", 0)
                   and x["eqPos2"] == self._value(patch, "EQ_EACH(2)", 0)), ["eq", "eq2", "amp"])
        a = flow.index("amp")
        return [x for x in flow[:a] + eq + flow[a + 1:] if x != "fv"]

    def _chain_texts(self, patch) -> list[str]:
        """The 9 CHAIN choices as orders, for the current pedal FX and send/return positions."""
        return [" > ".join(CHAIN_SHORT.get(x, x.upper()) for x in self._flow(patch, k)
                           if x not in ("eq", "eq2")) for k in range(9)]

    def order(self, patch=None) -> list[int]:
        """Section indexes in signal order; sections outside the chain (NS, SOLO, CONTOUR) last."""
        patch = patch or self.temp
        out = [CHAIN_ID[x] for x in self._flow(patch) if x in CHAIN_ID]
        return out + [i for i in range(len(SECTIONS)) if i not in out]

    # ---- decoded views -------------------------------------------------------
    def _type_text(self, i: int, patch) -> str:
        """The section's current type as BTS names it (e.g. 'T-SCREAM'), '' when it has none."""
        for e in self._entries(i, patch):
            if e.p["id"].endswith("_TYPE") and e.options:
                return self._display(e, decode(patch[e.block], e.p))
        return ""

    def _on(self, i: int, patch) -> bool:
        sw = SECTIONS[i][2]
        if sw:
            return bool(self._value(patch, *sw))
        if SECTIONS[i][4] == "contour":
            return self._value(patch, "CONTOUR_COM", 0) > 0
        return True

    def _color(self, i: int, patch) -> str:
        css, var = SECTIONS[i][1], SECTIONS[i][3]
        return css or VARIATION_CSS[self._variation(patch, var)]

    def _section(self, fx: int) -> int:
        if not 1 <= fx <= len(SECTIONS):
            raise KeyError(f"no block {fx}; the KATANA has blocks 1..{len(SECTIONS)}")
        return fx - 1

    def describe_fx(self, fx: int) -> dict:
        with self.lock:
            i = self._section(fx)
            same_layout = self.saved is not None and self._parts(i, self.saved) == self._parts(i, self.temp)
            params = []
            for e in self._entries(i, self.temp):
                v = decode(self.temp[e.block], e.p)
                sv = None
                if self.saved is not None and (same_layout or e.block in ("SW", "COLOR") or e.name == "OFF/ON"):
                    sv = decode(self.saved[e.block], e.p)
                params.append({"name": e.name, "value": v, "display": self._display(e, v),
                               "min": e.p["min"], "max": e.p["max"], "options": e.options,
                               "saved": sv, "live": e.live,
                               "changed": sv is not None and sv != v and not e.live})
            return {"fx": fx, "type": SECTIONS[i][0], "sub": self._type_text(i, self.temp),
                    "color": self._color(i, self.temp), "on": self._on(i, self.temp), "params": params,
                    "readonly": False}

    def slot_labels(self) -> list[str]:
        return self.labels

    def patch_name(self) -> str:
        return self.name_of(self.temp["COM"])

    def summary(self) -> dict:
        with self.lock:
            chain = [{"fx": i + 1, "type": SECTIONS[i][0], "sub": self._type_text(i, self.temp),
                      "color": self._color(i, self.temp), "on": self._on(i, self.temp),
                      "in_chain": SECTIONS[i][5] is not None}
                     for i in self.order()]
            return {"current": self.current, "label": self.label(), "name": self.patch_name(),
                    "edited": self.edited, "chain": chain, "straight": False, "readonly": False,
                    **self.history_summary()}

    # ---- live edits (edit buffer only; never stored) ---------------------------
    def _find(self, fx: int, name: str) -> Entry:
        i = self._section(fx)
        for e in self._entries(i, self.temp):
            if e.name.lower() == str(name).lower():
                return e
        names = ", ".join(e.name for e in self._entries(i, self.temp))
        raise KeyError(f"{SECTIONS[i][0]} (block {fx}) has no parameter '{name}'. It has: {names}")

    def _write(self, block: str, p: dict, value: int):
        data = encode(p, value)
        off, _, _ = self.t.blocks[block]
        self.port.dt1(add7(add7(EDIT_BUFFER, from7(off)), p["offset"]), data)
        self.temp[block][p["offset"]:p["offset"] + len(data)] = data

    def set_param(self, fx: int, name: str, value, who: str = "you", record: bool = True) -> dict:
        with self.lock:
            e = self._find(fx, name)
            lo, hi = e.p["min"], e.p["max"]
            if isinstance(value, str) and not value.lstrip("-").isdigit():
                up = [o.upper() for o in (e.options or [])]
                if value.upper() not in up:
                    raise ValueError(f"'{value}' is not an option of {e.name}: {e.options}")
                value = up.index(value.upper()) + lo
            value = int(value)
            if not lo <= value <= hi:
                raise ValueError(f"{e.name} must be {lo}..{hi}, got {value}")
            old = decode(self.temp[e.block], e.p)
            self._write(e.block, e.p, value)
            self.update_edited()
            if record and old != value:
                self.remember(Change(fx, e.name, old, value, who, e.block, e.p["offset"]))
                self._log_set(who, "set", fx, e, old, value)
        self.on_event("change", {"fx": fx})
        return {"fx": fx, "param": e.name, "old": old, "new": value, "display": self._display(e, value)}

    def set_switch(self, fx: int, on: bool, who: str = "you") -> dict:
        if SECTIONS[self._section(fx)][2] is None:
            raise ValueError(f"{SECTIONS[fx - 1][0]} has no on/off switch")
        return self.set_param(fx, "OFF/ON", 1 if on else 0, who=who)

    def record_change(self, fx: int, param: str, old: int, new: int, who: str = "you"):
        """One history entry for a change already sent (a whole knob drag in the window)."""
        with self.lock:
            if old == new:
                return
            e = self._find(fx, param)
            self.remember(Change(fx, e.name, old, new, who, e.block, e.p["offset"]))
            self._log_set(who, "set", fx, e, old, new)
        self.on_event("change", {"fx": fx})

    def apply_change(self, c: Change, value: int, who: str):
        """Undo/redo write straight to the stored address: the section may look different by now."""
        self._write(c.block, self._prm(c.block, c.offset), value)
        self.update_edited()

    def log_change(self, who: str, action: str, c: Change, old, new):
        self._log(who, action, fx=c.fx, type=SECTIONS[c.fx - 1][0], param=c.param, old=old, new=new)

    def set_chain(self, *_a, **_k):
        raise ValueError("The KATANA has nine fixed signal orders: set AMP's CHAIN parameter instead "
                         "(and the POSITION of PEDAL FX, SEND/RETURN, EQ and EQ 2).")

    def select_patch(self, n: int, who: str = "you"):
        """Switch the amp to memory n (0 = PANEL, 1..8 = A:CH1..B:CH4). Unsaved edits are lost."""
        if not 0 <= n < NUM_PATCHES:
            raise ValueError(f"patch must be 0..{NUM_PATCHES - 1} ({', '.join(self.labels)})")
        with self.lock:
            self._selecting = n
            try:
                self.port.dt1(PATCH_SELECT, [0, n], pause=0.5)
                self.reload()
                self._log(who, "patch")
            finally:
                time.sleep(0.2)                    # let a late echo find _selecting still set
                self._selecting = None

    # ---- the amp's own changes (editor mode) -------------------------------------
    def _unsolicited(self, addr, data):
        a = from7(addr)
        if a == from7(SETUP):                      # patch switched on the amp
            n = (data[0] & 15) << 4 | (data[1] & 15) if len(data) >= 2 else None
            if n is not None and n in (self.current, self._selecting):
                return                             # our own PATCH_SELECT echoed back
            time.sleep(0.3)
            self.reload()
            self._log("unit", "patch")
            return
        if a == from7(PATCH_WRITE):                # the player stored a memory on the amp
            time.sleep(0.5)
            with self.lock:
                self.names = self.read_names()
                self.reload()
            return
        base = from7(EDIT_BUFFER)
        if not base <= a < from7(patch_base(0)) or self.temp is None:
            return
        rel = a - base
        with self.lock:
            for start, b in reversed(self._block_at):
                if start <= rel and b in self.temp:
                    new = list(self.temp[b])
                    for k, x in enumerate(data):
                        if rel - start + k < len(new):
                            new[rel - start + k] = x
                    self._apply_unit(b, new)
                    break
        self.on_event("change", {"from": "unit"})

    def _apply_unit(self, block: str, new: list[int]):
        """The amp changed its own sound (knob, footswitch): take it over and log what moved."""
        old_patch = {k: list(v) for k, v in self.temp.items()}
        self.temp[block] = new
        self.update_edited()
        for i in range(len(SECTIONS)):
            for e in self._entries(i, old_patch):
                if e.block != block or e.live:
                    continue
                ov, nv = decode(old_patch[block], e.p), decode(new, e.p)
                if ov != nv:
                    self._log_set("unit", "set", i + 1, e, ov, nv, merge=True)

    # ---- diary -----------------------------------------------------------------
    def _log(self, who: str, action: str, **extra):
        self.log.add(who=who, action=action, patch=self.label(), name=self.patch_name(), device=self.title, **extra)

    def _log_set(self, who, action, fx, e: Entry, old, new, merge=False):
        self.log.add(merge_key=(who, fx, e.name) if merge else None, who=who, action=action,
                     patch=self.label(), name=self.patch_name(), device=self.title, fx=fx,
                     type=SECTIONS[fx - 1][0], param=e.name, old=old, new=new, display=self._display(e, new))

    # ---- backup -------------------------------------------------------------------
    def read_slot(self, n: int) -> dict[str, list[int]]:
        return {b: self._rq(patch_base(n), b) for b in self.t.blocks if "READONLY" not in b}

    @staticmethod
    def slot_name(blocks) -> str:
        return RolandUnit.name_of(blocks["COM"])

    def backup_layout(self) -> list:
        return [[b, list(off), size] for b, (off, _, size) in self.t.blocks.items() if "READONLY" not in b]
