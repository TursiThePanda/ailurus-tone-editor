"""Load the KATANA Gen 3 parameter tables from the user's installed BOSS TONE STUDIO at runtime.

As for the GX-10, nothing of Roland's is shipped: everything is read from the local BTS install.
Sources inside its html/ folder:
- js/config/address_map.js   every parameter: address, size, offset, min, max, PRMID name
- js/config/product_setting.js  model id (SysEx header) and BTS version
- js/config/resource.js      value lists (amp, booster and FX type names)
- export/item.json           which UI widget edits which parameter ("pid": "PATCH%DELAY(1)%5")
- export/layout.div          the BTS window as HTML: the label text of each widget and its value
                             texts ("stringer": a list of texts, or a JS format expression)
"""
from __future__ import annotations

import json
import re
import threading
from html.parser import HTMLParser
from pathlib import Path

from py_mini_racer import MiniRacer

from ..locate import BTSError, BTSProduct, locate
from ..rolandbts import FORMAT_JS, parse_address_map, read_product
from ..rolandbts import encode as encode_raw

KATANA3_BTS = BTSProduct(
    unit="KATANA Gen3", app="BOSS TONE STUDIO for KATANA Gen 3", env="KATANA3_BTS_HTML",
    setting="js/config/product_setting.js",
    required=("js/config/address_map.js", "js/config/resource.js", "export/item.json", "export/layout.div"),
    mac=("/Applications/BOSS/KATANA Gen 3/BOSS TONE STUDIO for KATANA Gen 3.app/Contents/Resources/html",))


def find_katana_root(chosen: str | None = None) -> Path:
    """The html folder of BOSS TONE STUDIO for KATANA Gen 3 (see locate.py for where we look)."""
    return locate(KATANA3_BTS, chosen)


class _Layout(HTMLParser):
    """Collect, per element id: its attributes, the texts of its <p> children and of its <a> options."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.el: dict[str, dict] = {}
        self._stack: list[tuple[str, str | None]] = []
        self._text: list[str] | None = None
        self._text_tag = ""

    def _owner(self) -> str | None:
        for _, i in reversed(self._stack):
            if i:
                return i
        return None

    VOID = {"br", "img", "input", "meta", "link", "hr", "source", "wbr", "area", "col", "embed"}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "br":
            if self._text is not None:
                self._text.append(" ")
            return
        if tag in self.VOID:
            return
        i = a.get("id")
        if i:
            self.el[i] = {"attrs": a, "p": [], "a": []}
        self._stack.append((tag, i))
        if tag in ("p", "a"):
            self._text, self._text_tag = [], tag

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if tag in ("p", "a") and self._text is not None:
            owner = self._owner()
            if owner:
                self.el[owner][self._text_tag].append(re.sub(r"\s+", " ", "".join(self._text)).strip())
            self._text = None
        while self._stack:
            t, _ = self._stack.pop()
            if t == tag:
                break

    def handle_data(self, data):
        if self._text is not None:
            self._text.append(data)


def pretty(prmid: str, prefix: str) -> str:
    """PRMID_PATCH_DELAY_HIGH_CUT -> 'HIGH CUT' (used only when BTS shows no label)."""
    return prmid[len(prefix):].replace("_", " ").strip() if prmid.startswith(prefix) else prmid


class KatanaTables:
    def __init__(self, root: Path):
        self.root = root
        self._lock = threading.Lock()
        cfg = root / "js" / "config"
        try:
            product = read_product(cfg / "product_setting.js")
            self.bts_version = product.version
            self.header = product.header
            self._read_address_map((cfg / "address_map.js").read_text(encoding="utf-8-sig"))
            self._read_resources((cfg / "resource.js").read_text(encoding="utf-8-sig"))
            self._read_widgets(root / "export")
        except BTSError:
            raise
        except Exception as e:  # format changed in a BTS update
            raise BTSError(f"Could not read KATANA BTS {getattr(self, 'bts_version', '?')} tables: {e}") from e
        self._ctx = MiniRacer()
        self._ctx.eval(FORMAT_JS)

    # ---- address map -------------------------------------------------------------
    def _read_address_map(self, js: str):
        amap = parse_address_map(js)
        self.tables = amap.tables
        if "PATCH" not in amap.children:
            raise BTSError("PATCH layout not found in address_map.js")
        # block name ('DELAY(4)') -> (offset bytes inside a patch, table name, size in bytes)
        self.blocks = amap.blocks("PATCH")
        # FX_DETAIL holds every FX type's params one after another, grouped by name prefix in type order
        self.fx_groups = self._groups("PATCH_FX_DETAIL", "PRMID_PATCH_FX_DETAIL_")
        self.pedal_groups = self._groups("PATCH_PEDALFX", "PRMID_PATCH_PEDALFX_")

    def _groups(self, table: str, prefix: str) -> list[str]:
        order: list[str] = []
        for r in self.tables.get(table, []):
            g = r["id"][len(prefix):].split("_")[0]
            if not order or order[-1] != g:
                order.append(g)
        return order

    # ---- value lists -------------------------------------------------------------
    def _read_resources(self, js: str):
        self.resources = [[x.strip() for x in t.split(",")]
                          for t in re.findall(r'\{\s*text:\s*"([^"]*)"\s*\}', js)]

        def find(first: str, n: int) -> list[str]:
            for r in self.resources:
                if r and r[0] == first and len(r) == n:
                    return r
            raise BTSError(f"value list starting with {first} ({n} items) not found in resource.js")

        self.fx_types = find("T.WAH", len(self.fx_groups))
        bmax = next(r["max"] for r in self.tables["PATCH_BOOSTER"] if r["id"].endswith("_TYPE"))
        self.booster_types = find("MID BOOST", bmax + 1)

    # ---- widgets: labels, option texts and value formats ---------------------------
    def _read_widgets(self, export: Path):
        items = json.loads((export / "item.json").read_text(encoding="utf-8-sig"))
        lay = _Layout()
        lay.feed((export / "layout.div").read_text(encoding="utf-8-sig"))
        el = lay.el
        self.ui: dict[tuple[str, int], dict] = {}   # (block base name, linear offset) -> widget info
        # editor widgets first; 'panel-' knobs only for what the editor pages do not show (amp section)
        for panel in (False, True):
            for wid, it in items.items():
                pid = it.get("pid") if isinstance(it, dict) else None
                if not pid or not pid.startswith("PATCH%") or pid.count("%") != 2:
                    continue
                if (wid.startswith("asgn-") or wid.endswith(("-spinner", "-watcher"))
                        or wid.startswith("panel-") != panel):
                    continue
                _, block, off = pid.split("%")
                key = (re.sub(r"\(\d+\)$", "", block), int(off))
                if key not in self.ui:
                    self.ui[key] = self._widget(wid, it, el)
        # AMP TYPE: the panel knob has no text list; BTS lights one of six labels instead
        js = (export.parent / "js" / "businesslogic" / "bts" / "effect_controller.js").read_text(encoding="utf-8-sig")
        amp_labels = re.findall(r"label:\s*'(amp-type-\w+-label)'", js)
        amp_names = [(el.get(i, {}).get("p") or [""])[0] for i in amp_labels]
        amp_type = next(r for r in self.tables["PATCH_AMP"] if r["id"] == "PRMID_PATCH_AMP_TYPE")
        if len(amp_names) == amp_type["max"] + 1 and all(amp_names):
            self.ui.setdefault(("AMP", amp_type["offset"]), {})["options_by_value"] = dict(enumerate(amp_names))
        # signal order: CHAIN (0-8) x pedal FX position x send/return position, then the two EQ positions
        cfg = (export.parent / "js" / "businesslogic" / "chain" / "chain_config.js").read_text(encoding="utf-8-sig")
        ctx = MiniRacer()
        try:
            ctx.eval("var window = this;")
            ctx.eval(cfg)
            self.chain_base = json.loads(ctx.eval("JSON.stringify(CHAIN_BASE_FLOW)"))
            self.chain_eq = json.loads(ctx.eval("JSON.stringify(CHAIN_EQ_FLOW)"))
        finally:
            ctx.close()

    @staticmethod
    def _widget(wid: str, it: dict, el: dict) -> dict:
        e = el.get(wid, {"attrs": {}, "p": [], "a": []})
        label_id = e["attrs"].get("description") or re.sub(r"-(dial|knob|slider|select-box|btn)$", "-label", wid)
        label = (el.get(label_id, {}).get("p") or [""])[0]
        info = {"label": label, "widget": wid}
        if wid.endswith("select-box"):
            texts = el.get(wid + "-box", {}).get("a", [])
            values = it.get("list_order") or list(range(len(texts)))
            if texts and len(values) == len(texts):
                info["options_by_value"] = dict(zip(values, texts, strict=True))
        st = el.get(it.get("stringer", ""), None)
        if st:
            fmt = st["attrs"].get("format") or ""
            if fmt:
                info["format"] = fmt.replace("<br>", " ")
            elif st["p"]:
                info["texts"] = st["p"]
        coef = e["attrs"].get("value-coef")
        if coef:
            info["coef"] = float(coef)
        return info

    # ---- public helpers ----------------------------------------------------------------
    def params(self, block: str, prefix: str | None = None) -> list[dict]:
        """Parameters of one patch block ('DELAY(4)'), each with label/options from the BTS window.
        `prefix` keeps only the params of one FX type (FX_DETAIL holds all types)."""
        _, table, _ = self.blocks[block]
        base = re.sub(r"\(\d+\)$", "", block)
        tprefix = "PRMID_" + table + "_"
        out = []
        for r in self.tables[table]:
            if prefix and not r["id"][len(tprefix):].startswith(prefix + "_"):
                continue
            ui = self.ui.get((base, r["offset"]), {})
            name = ui.get("label") or pretty(r["id"], tprefix + (prefix + "_" if prefix else ""))
            out.append(dict(r, name=name.upper(), ui=ui))
        return out

    def options(self, p: dict) -> list[str] | None:
        ui = p.get("ui", {})
        n = p["max"] - p["min"] + 1
        if "options_by_value" in ui:
            o = ui["options_by_value"]
            if all((p["min"] + i) in o for i in range(n)):
                return [o[p["min"] + i] for i in range(n)]
        texts = ui.get("texts") or []
        if len(texts) in (n, n + 1):   # the stringer's last <p> is the visible "current value" copy
            return texts[:n]
        if n == 2 and p["id"].endswith(("_SW", "_VARIATION")):
            return ["OFF", "ON"]
        return None

    def display(self, p: dict, value: int) -> str:
        opts = self.options(p)
        if opts:
            i = value - p["min"]
            if 0 <= i < len(opts):
                return opts[i]
        ui = p.get("ui", {})
        if ui.get("format"):
            with self._lock:
                out = self._ctx.call("__fmt", ui["format"], value)
            if out is not None:
                return out
        if ui.get("coef"):
            v = value * ui["coef"]
            return f"{v:g}"
        return str(value)

    def close(self):
        self._ctx.close()


def encode(p: dict, value: int) -> list[int]:
    """Value as shown -> the bytes the amp stores (inverse of katana3.decode)."""
    return encode_raw(value, p["kind"], p["n"], p["ofs"])
