"""BOSS GX-10 device profile: addresses, patch layout, decoding and live (temp) edits.

Facts proven on the unit 2026-11-06..12:
- 198 visible user patches U01-1..U66-3; internal slots 199/200 are hidden -> never touched.
- fxItem: writing byte 0 (type) resets the params, so type/switch and params are separate writes.
- Reads right after a write burst can return garbage -> pause and re-read.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .bts import Tables, layout
from .history import ActivityLog
from .roland import NotConnected, RolandPort, add7, from7

PORT_NAME = "GX-10"
HEADER = [0xF0, 0x41, 0x10, 0x00, 0x00, 0x00, 0x00, 0x0B]
VISIBLE_USER = 198              # U01-1 .. U66-3
TEMP = [0x10, 0, 0, 0]
USER_BASE = [0x20, 0, 0, 0]
NAMES_BASE = [0x50, 0, 0, 0]
SLOT_STRIDE = from7([6, 0, 0])
EDITOR_MODE = [0x7F, 0, 0, 1]
CURRENT_PATCH = [0, 0, 0, 0]
CHAIN_EDIT = [0, 0x20, 0, 3]    # SetupTemp ChainEditTrigger: BTS sends 1 before and 0 after a chain write
CHAIN_AT, CHAIN_LEN = 0x0C, 50  # efct: ChainTopItem + ChainNextItem0..48, stored value+1, 0 = end
BRANCH_TYPES = {"DIVIDER", "SPLITTER", "MIXER"}
CHAIN = "CHAIN"                 # pseudo parameter name for an order change in history/undo
NUM_FX = 20
LIVE_PARAMS = {"PEDAL POSITION"}  # follow the physical pedal; never count as an edit

# (name, offset as 7-bit address bytes, size)
BLOCKS = [("common", [0, 0, 0, 0], 129), ("led", [0, 0, 1, 0x40], 28)]
BLOCKS += [(f"assign{i + 1}", [0, 0, 2 + i // 2, 0x40 * (i % 2)], 45) for i in range(20)]
BLOCKS += [("efct", [0, 0, 0x0F, 0], 62)]
BLOCKS += [(f"fx{i + 1}", [0, 0, 0x11 + 2 * i, 0], 179) for i in range(NUM_FX)]
BLOCK_INDEX = {b[0]: i for i, b in enumerate(BLOCKS)}


def slot_label(n: int) -> str:
    """0-based user slot -> panel label U01-1."""
    return f"U{n // 3 + 1:02d}-{n % 3 + 1}"


def user_base(n: int) -> list[int]:
    return add7(USER_BASE, n * SLOT_STRIDE)


def read_value(block: list[int], p: dict) -> int:
    kind, n = layout(p.get("size"))
    a = p["address"]
    if kind == "nibbles":
        raw = 0
        for x in block[a:a + n]:
            raw = (raw << 4) | (x & 15)
    elif kind == "byte7":
        raw = (block[a] << 7) | block[a + 1]
    else:
        raw = block[a]
    return raw - (p.get("ofs") or 0)


def encode_value(p: dict, value: int) -> list[int]:
    kind, n = layout(p.get("size"))
    raw = value + (p.get("ofs") or 0)
    if kind == "nibbles":
        return [(raw >> (4 * (n - 1 - i))) & 15 for i in range(n)]
    if kind == "byte7":
        return [(raw >> 7) & 0x7F, raw & 0x7F]
    return [raw & 0x7F]


@dataclass
class Change:
    fx: int
    param: str
    old: int
    new: int
    who: str
    t: float = field(default_factory=time.time)


class GX10:
    kind = "gx10"
    title = "GX-10"
    port_name = PORT_NAME
    readonly = False
    supports_backup = True

    def __init__(self, tables: Tables, on_event=None, log: ActivityLog | None = None):
        self.t = tables
        self.log = log or ActivityLog(None)
        self.port = RolandPort(PORT_NAME, HEADER, on_unsolicited=self._unsolicited)
        self.on_event = on_event or (lambda kind, data: None)
        self.lock = threading.RLock()
        self.current: int | None = None        # 0-based patch number on the unit
        self.temp: list[list[int]] | None = None
        self.saved: list[list[int]] | None = None   # stored slot, for "changed vs saved"
        self.names: list[str] = []
        self.history: dict[int, dict] = {}     # patch -> {"undo": [...], "redo": [...]}
        self.edited = False
        self.error: str | None = None

    def poll(self):
        """The GX-10 reports panel changes by itself (editor mode): nothing to poll."""

    def patch_list(self) -> list[dict]:
        return [{"slot": i, "label": slot_label(i), "name": n} for i, n in enumerate(self.names)]

    @staticmethod
    def parse_slot(slot: str) -> int:
        """'U66-1' -> 0-based user slot 195."""
        try:
            bank, num = slot.upper().strip().lstrip("U").split("-")
            n = (int(bank) - 1) * 3 + int(num) - 1
        except ValueError:
            n, num = -1, "0"
        if not 0 <= n < VISIBLE_USER or not 1 <= int(num) <= 3:
            raise ValueError("slot must be U01-1 .. U66-3")
        return n

    # ---- connection ----------------------------------------------------------
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

    # ---- reads ---------------------------------------------------------------
    def read_names(self) -> list[str]:
        raw = []
        for start in range(0, VISIBLE_USER, 16):  # names are 16 bytes each, read in chunks
            cnt = min(16, VISIBLE_USER - start)
            raw += self.port.rq1(add7(NAMES_BASE, start * 16), cnt * 16)
        return [bytes(raw[i * 16:(i + 1) * 16]).decode("ascii", "replace").rstrip() for i in range(VISIBLE_USER)]

    def read_patch(self, base) -> list[list[int]]:
        return [self.port.rq1(add7(base, from7(off)), size) for _, off, size in BLOCKS]

    def read_current_number(self) -> int:
        b = self.port.rq1(CURRENT_PATCH, 4)
        return (b[0] << 12) | (b[1] << 8) | (b[2] << 4) | b[3]

    def reload(self):
        with self.lock:
            self.current = self.read_current_number()
            self.temp = self.read_patch(TEMP)
            self.saved = self.read_patch(user_base(self.current)) if self.current < VISIBLE_USER else None
            self.edited = self.saved is not None and self._differs()
            self.history.setdefault(self.current, {"undo": [], "redo": []})
        self.on_event("patch", {"current": self.current})

    def _live_masked(self, patch) -> list[list[int]]:
        """Copy with LIVE values blanked: they move by themselves and are not edits
        (LED block, expression pedal positions such as FOOT VOLUME's PEDAL POSITION)."""
        out = [list(b) for b in patch]
        out[BLOCK_INDEX["led"]] = []
        for fx in range(1, NUM_FX + 1):
            blk = out[BLOCK_INDEX[f"fx{fx}"]]
            for p in self.t.params(self.t.fx_type_name(blk[0])):
                if p["name"] in LIVE_PARAMS:
                    n = layout(p.get("size"))[1]
                    blk[p["address"]:p["address"] + n] = [0] * n
        return out

    def _differs(self) -> bool:
        return self._live_masked(self.temp) != self._live_masked(self.saved)

    # ---- decoded views -------------------------------------------------------
    def fx_block(self, patch, fx: int) -> list[int]:
        return patch[BLOCK_INDEX[f"fx{fx}"]]

    def chain(self) -> list[int]:
        """fx numbers (1-based) in signal order, by walking the chain linked list."""
        efct = self.temp[BLOCK_INDEX["efct"]]
        top = efct[0x0C] - 1
        nxt = [x - 1 for x in efct[0x0D:0x0D + 49]]
        order, seen, cur = [], set(), top
        while 0 <= cur < len(nxt) and cur not in seen:
            seen.add(cur)
            if cur < NUM_FX:
                order.append(cur + 1)
            cur = nxt[cur]
        # TODO: divider/splitter branches are not laid out yet; items off the main path are unused slots
        return order

    def is_straight(self) -> bool:
        """True when the chain is one line of effects: no divider/splitter/mixer on the path.
        Only such chains may be reordered until branches are decoded."""
        efct = self.temp[BLOCK_INDEX["efct"]]
        nxt = [x - 1 for x in efct[CHAIN_AT + 1:CHAIN_AT + CHAIN_LEN]]
        cur, seen = efct[CHAIN_AT] - 1, set()
        while cur != -1:
            if not 0 <= cur < NUM_FX or cur in seen or self._fx_type(cur + 1) in BRANCH_TYPES:
                return False
            seen.add(cur)
            cur = nxt[cur]
        return True

    def _chain_text(self, order: list[int]) -> str:
        return " > ".join(self._fx_type(fx) for fx in order)

    def _move_text(self, old: list[int], new: list[int]) -> str:
        """'BOOSTER (fx13) before COMPRESSOR (fx7)' when one block moved, else the whole order."""
        for fx in new:
            if [x for x in old if x != fx] == [x for x in new if x != fx]:
                i = new.index(fx)
                where = (f"before {self._fx_type(new[i + 1])} (fx{new[i + 1]})" if i + 1 < len(new)
                         else "to the end")
                return f"{self._fx_type(fx)} (fx{fx}) moved {where}"
        return self._chain_text(new)

    def _fx_type(self, fx: int, patch=None) -> str:
        return self.t.fx_type_name(self.fx_block(patch or self.temp, fx)[0])

    def describe_fx(self, fx: int) -> dict:
        blk = self.fx_block(self.temp, fx)
        sblk = self.fx_block(self.saved, fx) if self.saved else None
        ftype = self.t.fx_type_name(blk[0])
        params = []
        for p in self.t.params(ftype):
            if p["address"] + layout(p.get("size"))[1] > len(blk):
                continue
            v = read_value(blk, p)
            sv = read_value(sblk, p) if sblk and sblk[0] == blk[0] else None
            params.append({
                "name": p["name"], "value": v, "display": self.t.display(p, v),
                "min": p.get("min", 0), "max": p.get("max", 0),
                "options": self.t.options(p),
                "changed": sv is not None and sv != v and p["name"] not in LIVE_PARAMS, "saved": sv,
                "live": p["name"] in LIVE_PARAMS,
            })
        return {"fx": fx, "type": ftype, "color": self.t.color(ftype), "on": bool(blk[1]), "params": params}

    def label(self) -> str:
        return slot_label(self.current) if self.current < VISIBLE_USER else f"P{self.current - 199:02d}"

    def patch_name(self) -> str:
        return bytes(self.temp[0][:16]).decode("ascii", "replace").rstrip()

    def _log(self, who: str, action: str, fx: int | None = None, param: str | None = None,
             old=None, new=None, merge: bool = False):
        ev = {"who": who, "action": action, "patch": self.label(), "name": self.patch_name(), "device": self.title}
        if param == CHAIN:
            ev.update(param=CHAIN, old=old, new=new, display=self._move_text(old, new))
        elif fx is not None:
            ftype = self._fx_type(fx)
            ev.update(fx=fx, type=ftype, param=param, old=old, new=new)
            try:
                ev["display"] = self.t.display(self._param(fx, param), new)
            except KeyError:
                pass
        self.log.add(merge_key=(who, fx, param) if merge else None, **ev)

    def summary(self) -> dict:
        with self.lock:
            name = self.patch_name()
            fxs = []
            for fx in self.chain():
                blk = self.fx_block(self.temp, fx)
                ftype = self.t.fx_type_name(blk[0])
                fxs.append({"fx": fx, "type": ftype, "color": self.t.color(ftype), "on": bool(blk[1])})
            h = self.history.get(self.current, {"undo": [], "redo": []})
            return {
                "readonly": False,
                "current": self.current,
                "label": self.label(),
                "name": name, "edited": self.edited, "chain": fxs,
                "can_undo": bool(h["undo"]), "can_redo": bool(h["redo"]),
                "straight": self.is_straight(),
                "changes": [c.__dict__ for c in h["undo"][-50:]][::-1],
            }

    # ---- live edits (temp only; never stored) --------------------------------
    def _param(self, fx: int, name: str) -> dict:
        ftype = self._fx_type(fx)
        for p in self.t.params(ftype):
            if p["name"].lower() == name.lower():
                return p
        raise KeyError(f"{ftype} (fx{fx}) has no parameter '{name}'")

    def set_param(self, fx: int, name: str, value, who: str = "you", record: bool = True) -> dict:
        with self.lock:
            p = self._param(fx, name)
            opts = self.t.options(p)
            if isinstance(value, str) and not value.lstrip("-").isdigit():
                if not opts or value.upper() not in [o.upper() for o in opts]:
                    raise ValueError(f"'{value}' is not an option of {name}: {opts}")
                value = [o.upper() for o in opts].index(value.upper()) + (p.get("min") or 0)
            value = int(value)
            lo, hi = p.get("min", 0), p.get("max", 0)
            if not lo <= value <= hi:
                raise ValueError(f"{name} must be {lo}..{hi}, got {value}")
            blk = self.fx_block(self.temp, fx)
            old = read_value(blk, p)
            data = encode_value(p, value)
            a = p["address"]
            off = BLOCKS[BLOCK_INDEX[f"fx{fx}"]][1]
            self.port.dt1(add7(add7(TEMP, from7(off)), a), data)
            blk[a:a + len(data)] = data
            self.edited = self.saved is not None and self._differs()
            if record:
                self.record_change(fx, p["name"], old, value, who)
        self.on_event("change", {"fx": fx})
        return {"fx": fx, "param": p["name"], "old": old, "new": value, "display": self.t.display(p, value)}

    def set_switch(self, fx: int, on: bool, who: str = "you") -> dict:
        return self.set_param(fx, "OFF/ON", 1 if on else 0, who=who)

    def record_change(self, fx: int, param: str, old: int, new: int, who: str = "you"):
        """Store one history entry for a change already sent (e.g. a whole knob drag)."""
        with self.lock:
            if old == new:
                return
            h = self.history[self.current]
            h["undo"].append(Change(fx, param, old, new, who))
            h["redo"].clear()
            self._log(who, "set", fx, param, old, new)
        self.on_event("change", {"fx": fx})

    def undo(self, who: str = "you") -> dict | None:
        with self.lock:
            h = self.history.get(self.current)
            if not h or not h["undo"]:
                return None
            c = h["undo"].pop()
            self._apply(c, c.old, who)
            h["redo"].append(c)
            self._log(who, "undo", c.fx, c.param, c.new, c.old)
            return c.__dict__

    def redo(self, who: str = "you") -> dict | None:
        with self.lock:
            h = self.history.get(self.current)
            if not h or not h["redo"]:
                return None
            c = h["redo"].pop()
            self._apply(c, c.new, who)
            h["undo"].append(c)
            self._log(who, "redo", c.fx, c.param, c.old, c.new)
            return c.__dict__

    def _apply(self, c: Change, value, who: str):
        if c.param == CHAIN:
            self.set_chain(value, who=who, record=False)
        else:
            self.set_param(c.fx, c.param, value, who=who, record=False)

    def set_chain(self, order: list[int], who: str = "you", record: bool = True) -> dict:
        """Put the effects of the current (straight) chain in a new order, in TEMP only.
        `order` must name exactly the fx numbers on the chain now, e.g. [8, 7, 10, ...]."""
        with self.lock:
            if not self.is_straight():
                raise ValueError("This chain has a divider/splitter; reordering it is not supported yet")
            old = self.chain()
            order = [int(x) for x in order]
            if sorted(order) != sorted(old):
                raise ValueError(f"order must contain exactly the fx on the chain: {sorted(old)}")
            if order == old:
                return {"order": old}
            efct = self.temp[BLOCK_INDEX["efct"]]
            raw = list(efct[CHAIN_AT:CHAIN_AT + CHAIN_LEN])   # unused items keep their links
            # stored = item index + 1 = fx number; 0 = end. raw[0] is the top, raw[fx] is "next of fx".
            raw[0] = order[0]
            for a, b in zip(order, order[1:] + [0], strict=True):
                raw[a] = b
            base = add7(TEMP, from7(BLOCKS[BLOCK_INDEX["efct"]][1]))
            self.port.dt1(CHAIN_EDIT, [1])
            try:
                self.port.dt1(add7(base, CHAIN_AT), raw, pause=0.1)
            finally:
                self.port.dt1(CHAIN_EDIT, [0])
            efct[CHAIN_AT:CHAIN_AT + CHAIN_LEN] = raw
            self._verify_chain(base, raw)
            self.edited = self.saved is not None and self._differs()
            if record:
                h = self.history[self.current]
                h["undo"].append(Change(0, CHAIN, old, order, who))
                h["redo"].clear()
                self._log(who, "set", None, CHAIN, old, order)
        self.on_event("change", {"chain": order})
        return {"order": self.chain(), "chain": self._chain_text(self.chain())}

    def _verify_chain(self, base, raw):
        """Read the order back from the unit; if it disagrees, believe the unit and say so."""
        try:
            back = self.port.rq1(add7(base, CHAIN_AT), CHAIN_LEN)
        except NotConnected:   # demo mode: nothing to read back
            return
        if back != raw:
            self.temp[BLOCK_INDEX["efct"]][CHAIN_AT:CHAIN_AT + CHAIN_LEN] = back
            raise ValueError("The GX-10 reports a different chain order than was sent; showing what it has")

    def select_patch(self, n: int, who: str = "you"):
        """Switch the unit to user patch n (0-based). Unsaved temp edits are lost."""
        if not 0 <= n < VISIBLE_USER:
            raise ValueError(f"patch must be 0..{VISIBLE_USER - 1}")
        with self.lock:
            self.port.dt1(CURRENT_PATCH, [(n >> 12) & 15, (n >> 8) & 15, (n >> 4) & 15, n & 15], pause=0.6)
            self.reload()
            self._log(who, "patch")

    # ---- unit panel changes --------------------------------------------------
    def _unsolicited(self, addr, data):
        a = from7(addr)
        if a == 0 and len(data) >= 4:              # patch switched on the unit
            time.sleep(0.3)
            self.reload()
            self._log("unit", "patch")
        elif from7(TEMP) <= a < from7(USER_BASE):  # knob turned on the unit -> refresh temp
            self.apply_unit_write(a - from7(TEMP), data)

    def apply_unit_write(self, rel: int, data: list[int]):
        """The unit changed its current sound (panel knob, footswitch): update our copy and
        log which parameters moved, so the AI sees what the player did on the unit."""
        with self.lock:
            for i, (bname, off, size) in enumerate(BLOCKS):
                s = from7(off)
                if not s <= rel < s + size:
                    continue
                fx = int(bname[2:]) if bname.startswith("fx") else None
                old_chain = self.chain() if bname == "efct" else None
                old_type = self.temp[i][0]
                before = self._values(fx) if fx else None
                for k, x in enumerate(data):
                    if rel - s + k < size:
                        self.temp[i][rel - s + k] = x
                if old_chain is not None and self.chain() != old_chain:
                    self._log("unit", "set", None, CHAIN, old_chain, self.chain())
                if not fx:
                    continue
                if self.temp[i][0] != old_type:   # effect type changed: its params were reset
                    self._log("unit", "set", fx, "TYPE", self.t.fx_type_name(old_type), self._fx_type(fx))
                    continue
                for name, v in self._values(fx).items():
                    if before.get(name) != v and name not in LIVE_PARAMS:
                        self._log("unit", "set", fx, name, before.get(name), v, merge=True)
            self.edited = self.saved is not None and self._differs()
        self.on_event("change", {"from": "unit"})

    def _values(self, fx: int) -> dict:
        blk = self.fx_block(self.temp, fx)
        return {p["name"]: read_value(blk, p) for p in self.t.params(self.t.fx_type_name(blk[0]))
                if p["address"] + layout(p.get("size"))[1] <= len(blk)}
