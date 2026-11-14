"""BOSS GX-10 device profile: addresses, patch layout, decoding and live (temp) edits.

Facts proven on the unit 2026-11-06..12:
- 198 visible user patches U01-1..U66-3; internal slots 199/200 are hidden -> never touched.
- fxItem: writing byte 0 (type) resets the params, so type/switch and params are separate writes.
- Reads right after a write burst can return garbage -> pause and re-read.
- WRITE (BTS): DT1 7F 00 01 04 = [n >> 7, n & 0x7F] stores TEMP as user patch n; the unit
  answers with the same DT1 and is then on patch n. Sent only by save() (unit.py).
"""
from __future__ import annotations

import time

from .. import ir
from ..roland import NotConnected, add7, from7
from ..rolandbts import decode, encode
from ..unit import Change, RolandUnit
from .gx10_bts import GX10_BTS, Tables, find_bts_root, layout

PORT_NAME = "GX-10"
HEADER = [0xF0, 0x41, 0x10, 0x00, 0x00, 0x00, 0x00, 0x0B]   # = what product_setting.js gives
VISIBLE_USER = 198              # U01-1 .. U66-3
TEMP = [0x10, 0, 0, 0]
USER_BASE = [0x20, 0, 0, 0]
NAMES_BASE = [0x50, 0, 0, 0]
SLOT_STRIDE = from7([6, 0, 0])
CURRENT_PATCH = [0, 0, 0, 0]
PATCH_WRITE = [0x7F, 0, 1, 4]
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


def nibbles4(n: int) -> list[int]:
    """Patch number as the GX-10 sends and takes it: four 4-bit nibbles."""
    return [(n >> 12) & 15, (n >> 8) & 15, (n >> 4) & 15, n & 15]


def read_value(block: list[int], p: dict) -> int:
    kind, n = layout(p.get("size"))
    a = p["address"]
    return decode(block[a:a + n], kind, p.get("ofs") or 0)


def encode_value(p: dict, value: int) -> list[int]:
    kind, n = layout(p.get("size"))
    return encode(value, kind, n, p.get("ofs") or 0)


class GX10(RolandUnit):
    kind = "gx10"
    title = "GX-10"
    aliases = ("gx-10", "gx")
    hint = "Plug it straight into the computer, not through a hub."
    bts = GX10_BTS
    port_name = PORT_NAME
    backup_prefix = "GX-10"

    @classmethod
    def load_tables(cls, bts_folder: str | None) -> Tables:
        return Tables(find_bts_root(bts_folder))

    # ---- backup (see backup.BulkBackup) --------------------------------------
    def read_slot(self, n: int) -> list[list[int]]:
        return self.read_patch(user_base(n))

    @staticmethod
    def slot_name(blocks) -> str:
        return RolandUnit.name_of(blocks[0])

    @staticmethod
    def backup_layout() -> list:
        return [[b[0], b[1], b[2]] for b in BLOCKS]

    def slot_labels(self) -> list[str]:
        return [slot_label(n) for n in range(VISIBLE_USER)]   # hidden slots 199/200 are never listed

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

    # ---- reads ---------------------------------------------------------------
    def read_names(self) -> list[str]:
        raw = []
        for start in range(0, VISIBLE_USER, 16):  # names are 16 bytes each, read in chunks
            cnt = min(16, VISIBLE_USER - start)
            raw += self.port.rq1(add7(NAMES_BASE, start * 16), cnt * 16)
        return [self.name_of(raw[i * 16:(i + 1) * 16]) for i in range(VISIBLE_USER)]

    def read_patch(self, base) -> list[list[int]]:
        return [self.port.rq1(add7(base, from7(off)), size) for _, off, size in BLOCKS]

    def read_current_number(self) -> int:
        b = self.port.rq1(CURRENT_PATCH, 4)
        return (b[0] << 12) | (b[1] << 8) | (b[2] << 4) | b[3]

    def read_current_sound(self) -> list[list[int]]:
        return self.read_patch(TEMP)

    def read_saved(self, n: int):
        return self.read_patch(user_base(n)) if n < VISIBLE_USER else None

    def live_masked(self, patch) -> list[list[int]]:
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
        return self.name_of(self.temp[0])

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
            return {"readonly": False, "current": self.current, "label": self.label(),
                    "name": name, "edited": self.edited, "chain": fxs, "straight": self.is_straight(),
                    **self.history_summary()}

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
            self.update_edited()
            if record:
                self.record_change(fx, p["name"], old, value, who)
        self.on_event("change", {"fx": fx})
        return {"fx": fx, "param": p["name"], "old": old, "new": value, "display": self.t.display(p, value)}

    def record_change(self, fx: int, param: str, old: int, new: int, who: str = "you"):
        """Store one history entry for a change already sent (e.g. a whole knob drag)."""
        with self.lock:
            if old == new:
                return
            self.remember(Change(fx, param, old, new, who))
            self._log(who, "set", fx, param, old, new)
        self.on_event("change", {"fx": fx})

    def apply_change(self, c: Change, value, who: str):
        if c.param == CHAIN:
            self.set_chain(value, who=who, record=False)
        else:
            self.set_param(c.fx, c.param, value, who=who, record=False)

    def log_change(self, who: str, action: str, c: Change, old, new):
        self._log(who, action, c.fx, c.param, old, new)

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
            self.update_edited()
            if record:
                self.remember(Change(0, CHAIN, old, order, who))
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
        self.switch_patch(n, lambda: self.port.dt1(CURRENT_PATCH, nibbles4(n), pause=0.6), who)

    # ---- saving (unit.save() is the only caller) -------------------------------
    def writable_slots(self) -> list[int]:
        return list(range(VISIBLE_USER))           # never the hidden slots 199/200

    def set_name(self, name: str):
        raw = [ord(c) for c in name.ljust(16)[:16]]
        self.port.dt1(TEMP, raw, pause=0.1)
        self.temp[0][:16] = raw

    def store(self, n: int):
        if n not in self.writable_slots():
            raise ValueError(f"patch must be 0..{VISIBLE_USER - 1}")
        try:
            self.port.dt1_ack(PATCH_WRITE, [n >> 7, n & 0x7F], timeout=5.0)
        except TimeoutError:                       # still storing the previous save: once more
            time.sleep(2.0)
            self.port.dt1_ack(PATCH_WRITE, [n >> 7, n & 0x7F], timeout=5.0)

    def load_into_current(self, blocks):
        """Write a whole patch into TEMP. An effect block goes in two steps (type + switch,
        then the parameters): writing the type byte resets that effect's parameters."""
        for (bname, off, size), data in zip(BLOCKS, blocks, strict=True):
            # led = OnOffState: the unit computes it from the effect switches when it WRITEs
            if bname == "led" or len(data) != size:
                continue
            base = add7(TEMP, from7(off))
            if bname.startswith("fx"):
                self.port.dt1(base, data[:2], pause=0.4)
                self.port.dt1(add7(base, 2), data[2:], pause=0.1)
            elif bname == "efct":
                self.port.dt1(CHAIN_EDIT, [1])
                try:
                    self.port.dt1(base, data, pause=0.1)
                finally:
                    self.port.dt1(CHAIN_EDIT, [0])
            else:
                self.port.dt1(base, data, pause=0.1)
        time.sleep(0.5)

    # ---- cabinet IRs: 16 user IR slots (format and transfer in ir.py) -----------
    def ir_names(self) -> list[str]:
        """Names of USER 1..16. An empty slot is called "USER n"; the IR itself cannot be read."""
        with self.lock:
            raw = [self.port.rq1(ir.slot_base(n), ir.NAME_LEN) for n in range(ir.NUM_SLOTS)]
        return ["".join(chr(c) if 32 <= c < 127 else " " for c in r).rstrip() for r in raw]

    @staticmethod
    def ir_slot_empty(slot: int, name: str) -> bool:
        return name.strip().upper() in ("", f"USER {slot + 1}")

    def rename_ir(self, slot: int, name: str, who: str = "you") -> dict:
        ir.slot_base(slot)                         # USER 1..16 only
        name = ir.clean_name(name)
        with self.lock:
            old = self.ir_names()[slot]
            self.port.dt1(ir.slot_base(slot), ir.name_bytes(name), pause=0.2)
            got = self.ir_names()[slot]
        if got != name:
            raise RuntimeError(f"USER {slot + 1} reads back as \"{got}\", not \"{name}\".")
        self.log.add(who=who, action="ir_rename", patch=f"IR USER {slot + 1}", name=name, old=old, device=self.title)
        self.on_event("ir", {})
        return {"slot": slot, "name": name}

    def load_ir(self, slot: int, name: str, words, who: str = "you", replace: bool = False) -> dict:
        """Write a converted IR (ir.convert) into USER slot+1. The old IR cannot be read back, so
        a slot that holds one is overwritten only with replace=True (the player confirmed)."""
        ir.slot_base(slot)                         # USER 1..16 only
        name = ir.clean_name(name)
        if len(words) != ir.NUM_WORDS:
            raise ValueError(f"An IR has {ir.NUM_WORDS} values, not {len(words)}.")
        with self.lock:
            old = self.ir_names()[slot]
            if not replace and not self.ir_slot_empty(slot, old):
                raise ValueError(f"IR USER {slot + 1} holds \"{old}\", which cannot be backed up from the "
                                 f"unit. Confirm that it may be replaced.")
            ir.send(self.port, slot, name, words)
            got = self.ir_names()[slot]
        if got != name:
            raise RuntimeError(f"The IR was sent, but USER {slot + 1} reads back as \"{got}\".")
        self.log.add(who=who, action="ir_load", patch=f"IR USER {slot + 1}", name=name, old=old, device=self.title)
        self.on_event("ir", {})
        return {"slot": slot, "name": name, "replaced": old}

    # ---- unit panel changes --------------------------------------------------
    def _unsolicited(self, addr, data):
        a = from7(addr)
        if a == 0 and len(data) >= 4:              # patch switched on the unit (or our own select echoed)
            self.unit_switched_patch((data[0] & 15) << 12 | (data[1] & 15) << 8 | (data[2] & 15) << 4 | data[3] & 15)
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
            self.update_edited()
        self.on_event("change", {"from": "unit"})

    def _values(self, fx: int) -> dict:
        blk = self.fx_block(self.temp, fx)
        return {p["name"]: read_value(blk, p) for p in self.t.params(self.t.fx_type_name(blk[0]))
                if p["address"] + layout(p.get("size"))[1] <= len(blk)}
