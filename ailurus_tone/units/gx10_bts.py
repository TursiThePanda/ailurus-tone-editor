"""Load the GX-10 parameter tables from the user's installed BOSS TONE STUDIO at runtime.

Nothing of Roland's is shipped with this app: the JS files are read from the local BTS
install and evaluated in an embedded JS engine (mini-racer / V8) with a stubbed `window`.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from py_mini_racer import MiniRacer

from ..locate import BTSError, BTSProduct, locate
from ..rolandbts import FORMAT_JS, STUB_JS, eval_tolerant, read_product

GX10_BTS = BTSProduct(
    unit="GX-10", app="BOSS TONE STUDIO for GX-10", env="GX10_BTS_JS",
    setting="config/product_setting.js", required=("config/effect_parameter.js",),
    mac=("/Applications/BOSS/GX-10/BOSS TONE STUDIO for GX-10.app/Contents/Resources/html/js",),
    html_sub="js",
    download="https://static.roland.com/assets/media/zip/gx10_bts_w102.zip",                 # 1.0.2
    releases="https://tw.boss.info/support/by_product/gx-10/updates_drivers/",
    licence_page="https://www.boss.info/global/support/by_product/gx-10/updates_drivers/")

SIZE_CONSTANTS = {  # from utilities/constant.js; byte layout of each size code
    0x10000: ("byte", 1), 0x10001: ("byte", 1), 0x10002: ("byte", 1), 0x10003: ("byte", 1),
    0x10004: ("byte", 1), 0x10005: ("byte", 1), 0x10006: ("byte", 1),
    0x10007: ("nibbles", 2), 0x1000B: ("byte7", 2), 0x10008: ("nibbles", 4),
}


def find_bts_root(chosen: str | None = None) -> Path:
    """The html/js folder of BOSS TONE STUDIO for GX-10 (see locate.py for where we look)."""
    return locate(GX10_BTS, chosen)


class Tables:
    """Effect parameter tables, FX type list and option lists, as read from BTS."""

    def __init__(self, root: Path):
        self.root = root
        self._lock = threading.Lock()
        cfg = root / "config"
        product = read_product(cfg / "product_setting.js")
        if product.communication_level is None:
            raise BTSError("product_setting.js has an unexpected format (communicationLevel missing)")
        self.communication_level = product.communication_level
        self.bts_version = product.version
        self.header = product.header

        ctx = MiniRacer()
        ctx.eval(f"var window = this; window.ProductSetting = {{communicationLevel: {self.communication_level}}};"
                 "var Parameter = {value: function(){return 0;}};")
        ctx.eval(STUB_JS)
        try:
            for f in (root / "utilities" / "constant.js", cfg / "effect_parameter.js", cfg / "resource.js"):
                eval_tolerant(ctx, f.read_text(encoding="utf-8-sig"))
            self.effects = json.loads(ctx.eval("JSON.stringify(window.EFFECT_PARAMETERS)"))
            self.resources = json.loads(ctx.eval("JSON.stringify(Resource())"))
        except Exception as e:  # format changed in a BTS update
            ctx.close()
            raise BTSError(f"Could not read BTS {self.bts_version} tables: {e}") from e
        ctx.eval(FORMAT_JS)
        self._ctx = ctx

        chain_js = (root / "businesslogic" / "chain" / "chain_controller.js").read_text(encoding="utf-8-sig")
        lists = re.findall(r'RESOURCE_FX_TYPE\s*=\s*\{\s*text:\s*"([^"]+)"', chain_js)
        if not lists:
            raise BTSError("FX type list not found in chain_controller.js")
        # the last assignment is the one for communicationLevel >= 3
        idx = -1 if self.communication_level >= 3 else 0
        self.fx_types = [t.strip() for t in lists[idx].split(",")]

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
