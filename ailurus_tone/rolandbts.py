"""What every BOSS TONE STUDIO (BTS) has in common, read from the user's install at runtime.

Each BOSS unit has its own BTS, but they are built on one Roland framework. The parts shared
by every BTS seen so far (GX-10, KATANA Gen 3) live here, so a new unit needs only its own
quirks:

- config/product_setting.js  product name, BTS version, model id + device id (= SysEx header)
- config/address_map.js      every parameter: address, size code, offset, min, max, name, and
                             the tree of blocks ({addr, size: 0, child: TABLE, name})
- values: how a parameter's bytes become a number (the size codes INTEGER1x7, INTEGER2x4 ...)
- an embedded JS engine (mini-racer / V8) for files that must be evaluated, not parsed

Nothing of Roland's is shipped with this app; see locate.py for where the install is found.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .locate import BTSError

# ---- product_setting.js ---------------------------------------------------------------------


@dataclass(frozen=True)
class ProductInfo:
    name: str                  # 'GX-10', 'KATANA Gen3'
    version: str               # BTS version, e.g. '1.1.0'
    model_id: list[int]        # GX-10: 00 00 00 00 0B, KATANA Gen 3: 01 05 07
    device_id: int             # 0x10 on both
    communication_level: int | None

    @property
    def header(self) -> list[int]:
        """Roland SysEx header: F0, Roland (41), device id, model id."""
        return [0xF0, 0x41, self.device_id] + self.model_id


def _field(js: str, key: str) -> str | None:
    m = re.search(rf"\b{key}:\s*'([^']*)'", js)
    return m.group(1) if m else None


def read_product(path: Path) -> ProductInfo:
    """Parse product_setting.js (file path)."""
    try:
        js = path.read_text(encoding="utf-8-sig")
    except OSError as e:
        raise BTSError(f"Cannot read {path}: {e}") from e
    name, mid, dev = _field(js, "name"), _field(js, "modelId"), _field(js, "deviceId")
    if not (name and mid and dev):
        raise BTSError(f"{path.name} has an unexpected format (name / modelId / deviceId missing)")
    level = re.search(r"communicationLevel:\s*(\d+)", js)
    return ProductInfo(name=name, version=_field(js, "version") or "?", model_id=list(bytes.fromhex(mid)),
                       device_id=int(dev, 16), communication_level=int(level.group(1)) if level else None)


# ---- values -------------------------------------------------------------------------------------

# size code in address_map.js -> (kind, byte count). INTEGERaxb = a bytes of b useful bits each.
SIZE_CODES = {"INTEGER1x7": ("byte", 1), "INTEGER2x4": ("nibbles", 2), "INTEGER2x7": ("byte7", 2),
              "INTEGER4x4": ("nibbles", 4)}


def decode(raw: list[int], kind: str, ofs: int = 0) -> int:
    """Bytes as the unit stores them -> the value as BTS shows it (stored = shown + ofs)."""
    if kind == "nibbles":
        v = 0
        for x in raw:
            v = (v << 4) | (x & 15)
    elif kind == "byte7":
        v = (raw[0] << 7) | raw[1]
    else:
        v = raw[0]
    return v - ofs


def encode(value: int, kind: str, n: int, ofs: int = 0) -> list[int]:
    """Inverse of decode()."""
    raw = value + ofs
    if kind == "nibbles":
        return [(raw >> (4 * (n - 1 - i))) & 15 for i in range(n)]
    if kind == "byte7":
        return [(raw >> 7) & 0x7F, raw & 0x7F]
    if kind == "text":
        raise ValueError("text parameters are not edited here")
    return [raw & 0x7F]


# ---- address_map.js -------------------------------------------------------------------------------

def addr_bytes(hex8: str) -> list[int]:
    """address_map writes 4 raw 7-bit BYTES as one literal: '20100000' -> [0x20, 0x10, 0, 0].
    It is NOT a 28-bit number (to7() of it would give the wrong address)."""
    return list(bytes.fromhex(hex8))


TABLE = re.compile(r"(?:var\s+|this\.)(\w+)\s*=\s*\[(.*?)\n\s*\];", re.S)
LINE_COMMENT = re.compile(r"//[^'\n]*$", re.M)   # BTS comments out whole tables; no // inside strings
OBJECT = re.compile(r"\{([^{}]*)\}")
PAIR = re.compile(r"(\w+)\s*:\s*('[^']*'|0x[0-9A-Fa-f]+|-?\d+|\w+)")


@dataclass
class AddressMap:
    """tables: table name -> [row]; a row is {offset (linear, inside its block), kind, n, ofs,
    min, max, id}. children: table name -> [(offset bytes, child table, block name)].
    The unit's whole memory map is the table `root` (`this.root = [...]` in the file)."""
    tables: dict[str, list[dict]]
    children: dict[str, list[tuple[list[int], str, str]]]

    def roots(self) -> list[str]:
        used = {c for kids in self.children.values() for _, c, _ in kids}
        return [t for t in self.children if t not in used]

    def blocks(self, parent: str) -> dict[str, tuple[list[int], str, int]]:
        """Block name -> (offset bytes inside `parent`, its table, size in bytes) for one level."""
        out = {}
        for off, child, name in self.children.get(parent, []):
            rows = self.tables.get(child, [])
            out[name] = (off, child, max((r["offset"] + r["n"] for r in rows), default=0))
        return out


def _value(tok: str):
    if tok.startswith("'"):
        return tok[1:-1]
    if tok.startswith("0x"):
        return tok[2:]
    if tok.lstrip("-").isdigit():
        return int(tok)
    return tok                                     # a name: INTEGER1x7, a child table


def parse_address_map(js: str) -> AddressMap:
    """Read every `var NAME = [ {...}, ... ];` table. Field order differs between BTS builds
    (the GX-10 lists min, max, init; the KATANA init, min, max), so fields are read by key."""
    tables: dict[str, list[dict]] = {}
    children: dict[str, list[tuple[list[int], str, str]]] = {}
    for tname, body in TABLE.findall(LINE_COMMENT.sub("", js)):
        rows, kids = [], []
        for obj in OBJECT.findall(body):
            f = {k: _value(v) for k, v in PAIR.findall(obj)}
            if "addr" not in f or "name" not in f:
                continue
            off = addr_bytes(str(f["addr"]).rjust(8, "0"))
            if "child" in f:
                kids.append((off, f["child"], f["name"]))
                continue
            size = f.get("size")
            if isinstance(size, int):
                kind, n = "text", size
            elif size in SIZE_CODES:
                kind, n = SIZE_CODES[size]
            else:
                raise BTSError(f"unknown size {size} in {tname} of address_map.js")
            rows.append({"offset": (off[2] << 7) | off[3], "kind": kind, "n": n, "ofs": int(f.get("ofs", 0)),
                         "min": int(f.get("min", 0)), "max": int(f.get("max", 0)), "id": f["name"],
                         "init": f.get("init")})
        if rows:
            tables[tname] = rows
        if kids:
            children[tname] = kids
    if not tables:
        raise BTSError("address_map.js has an unexpected format (no parameter tables found)")
    return AddressMap(tables, children)


# ---- JS evaluation ------------------------------------------------------------------------------

# UI-only globals the table files mention (e.g. NUMPAD_TYPE.withText) are stubbed: any property
# read on a stub returns a plain string, so JSON export stays clean.
STUB_JS = ("function __stub(n){return new Proxy({}, {get:function(t,k){"
           "return k===Symbol.toPrimitive?undefined:n+'.'+String(k);}});}")
FORMAT_JS = "function __fmt(expr, value) { try { return String(eval(expr)); } catch (e) { return null; } }"


def eval_tolerant(ctx, code: str, tries: int = 40):
    """Evaluate a BTS file, defining every unknown global it touches as a stub."""
    for _ in range(tries):
        try:
            return ctx.eval(code)
        except Exception as e:
            m = re.search(r"ReferenceError: (\w+) is not defined", str(e))
            if not m:
                raise
            ctx.eval(f"var {m.group(1)} = __stub('{m.group(1)}');")
    raise BTSError("too many unknown names in BTS tables")
