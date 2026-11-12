"""Load the GX-10 parameter tables from the user's installed BOSS TONE STUDIO at runtime.

Nothing of Roland's is shipped with this app: the JS files are read from the local BTS
install and evaluated in an embedded JS engine (mini-racer / V8) with a stubbed `window`.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
from pathlib import Path

from py_mini_racer import MiniRacer

# Where BTS keeps its html/js tree. Windows path is not known yet (TODO, verify on a real install).
CANDIDATES = {
    "darwin": [
        "/Applications/BOSS/GX-10/BOSS TONE STUDIO for GX-10.app/Contents/Resources/html/js",
    ],
    "win32": [],
    "linux": [],
}

SIZE_CONSTANTS = {  # from utilities/constant.js; byte layout of each size code
    0x10000: ("byte", 1), 0x10001: ("byte", 1), 0x10002: ("byte", 1), 0x10003: ("byte", 1),
    0x10004: ("byte", 1), 0x10005: ("byte", 1), 0x10006: ("byte", 1),
    0x10007: ("nibbles", 2), 0x1000B: ("byte7", 2), 0x10008: ("nibbles", 4),
}


class BTSError(RuntimeError):
    pass


def find_bts_root(override: str | None = None) -> Path:
    paths = [override] if override else []
    paths += [os.environ.get("GX10_BTS_JS", "")] + CANDIDATES.get(sys.platform, [])
    for p in paths:
        if p and (Path(p) / "config" / "effect_parameter.js").is_file():
            return Path(p)
    raise BTSError(
        "BOSS TONE STUDIO for GX-10 was not found. Install it from boss.info "
        "(it does not need to run), or set GX10_BTS_JS to its html/js folder."
    )


class Tables:
    """Effect parameter tables, FX type list and option lists, as read from BTS."""

    def __init__(self, root: Path):
        self.root = root
        self._lock = threading.Lock()
        cfg = root / "config"
        product = (cfg / "product_setting.js").read_text(encoding="utf-8-sig")
        m = re.search(r"communicationLevel:\s*(\d+)", product)
        v = re.search(r"version:\s*'([^']+)'", product)
        if not m:
            raise BTSError("product_setting.js has an unexpected format (communicationLevel missing)")
        self.communication_level = int(m.group(1))
        self.bts_version = v.group(1) if v else "?"

        ctx = MiniRacer()
        ctx.eval(f"var window = this; window.ProductSetting = {{communicationLevel: {self.communication_level}}};"
                 "var Parameter = {value: function(){return 0;}};")
        # UI-only globals the table files mention (e.g. NUMPAD_TYPE.withText) are stubbed:
        # any property read on a stub returns a plain string, so JSON export stays clean.
        ctx.eval("function __stub(n){return new Proxy({}, {get:function(t,k){"
                 "return k===Symbol.toPrimitive?undefined:n+'.'+String(k);}});}")
        try:
            for f in (root / "utilities" / "constant.js", cfg / "effect_parameter.js", cfg / "resource.js"):
                self._eval_tolerant(ctx, f.read_text(encoding="utf-8-sig"))
            self.effects = json.loads(ctx.eval("JSON.stringify(window.EFFECT_PARAMETERS)"))
            self.resources = json.loads(ctx.eval("JSON.stringify(Resource())"))
        except Exception as e:  # format changed in a BTS update
            ctx.close()
            raise BTSError(f"Could not read BTS {self.bts_version} tables: {e}") from e
        ctx.eval("function __fmt(expr, value) { try { return String(eval(expr)); } catch (e) { return null; } }")
        self._ctx = ctx

        chain_js = (root / "businesslogic" / "chain" / "chain_controller.js").read_text(encoding="utf-8-sig")
        lists = re.findall(r'RESOURCE_FX_TYPE\s*=\s*\{\s*text:\s*"([^"]+)"', chain_js)
        if not lists:
            raise BTSError("FX type list not found in chain_controller.js")
        # the last assignment is the one for communicationLevel >= 3
        idx = -1 if self.communication_level >= 3 else 0
        self.fx_types = [t.strip() for t in lists[idx].split(",")]

    @staticmethod
    def _eval_tolerant(ctx, code: str, tries: int = 40):
        for _ in range(tries):
            try:
                return ctx.eval(code)
            except Exception as e:
                m = re.search(r"ReferenceError: (\w+) is not defined", str(e))
                if not m:
                    raise
                ctx.eval(f"var {m.group(1)} = __stub('{m.group(1)}');")
        raise BTSError("too many unknown names in BTS tables")

    def close(self):
        self._ctx.close()

    # ---- helpers -------------------------------------------------------------
    def fx_type_name(self, index: int) -> str:
        return self.fx_types[index] if 0 <= index < len(self.fx_types) else f"TYPE {index}"

    def color(self, fx_type: str) -> str:
        """BTS colour family of an effect (e.g. 'yellow' for drives), for colour-coding blocks."""
        return self.effects.get(fx_type, {}).get("color") or "gray_blank"

    def params(self, fx_type: str) -> list[dict]:
        return [p for p in self.effects.get(fx_type, {}).get("parameters", [])
                if isinstance(p.get("address"), int)]

    def options(self, p: dict) -> list[str] | None:
        rid = p.get("resourceId")
        if isinstance(rid, int) and 0 <= rid < len(self.resources):
            text = self.resources[rid].get("text")
            if isinstance(text, list) and len(text) > 1:
                return text
        tv = p.get("templateValue", "")
        if isinstance(tv, str) and "," in tv and not re.match(r"^\s*-?\d+\s*-\s*-?\d+", tv.split(",")[0]):
            return [x.strip() for x in tv.split(",")]
        return None

    def display(self, p: dict, value: int) -> str:
        opts = self.options(p)
        if opts:
            i = value - (p.get("min") or 0)
            if 0 <= i < len(opts):
                return opts[i]
        fmt = p.get("format")
        if fmt:
            with self._lock:
                out = self._ctx.call("__fmt", fmt, value)
            if out is not None:
                return out
        return str(value)


def layout(size) -> tuple[str, int]:
    """(kind, byte count) for a BTS size code; plain small ints are raw byte counts."""
    if isinstance(size, int) and size in SIZE_CONSTANTS:
        return SIZE_CONSTANTS[size]
    return ("nibbles", 4)
