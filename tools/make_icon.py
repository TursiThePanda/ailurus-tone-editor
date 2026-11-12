"""Build the app icon: David's red panda as the logo on a guitar pedal.

    .venv/bin/python tools/make_icon.py

Follows Apple's current app icon guidelines (macOS 26+): the source is a layered Icon
Composer document, assets/AppIcon.icon, with SQUARE, UNMASKED 1024 px layers - the
system applies the rounded shape, Liquid Glass and the default/dark/clear/tinted looks.
  background  fill in icon.json (burnt rust; darker in the dark appearance)
  enclosure.png  the pedal box; graphite in the light look, burnt rust in the dark look
                 (macOS darkens the background to near black there, so the colours swap)
  hardware.png   three knobs, LED, round stomp switch in a hex nut
  panda.png   David's own panda artwork (assets/panda-logo.png), used exactly as supplied

Outputs (assets/build/):
  Assets.car + AppIcon.icns   compiled by Xcode's actool (icns = Apple's own render for
                              older macOS); the icns is also copied to ailurus_tone/web/
  icon.ico                    Windows
  icon-1024.png               flat render (Apple's own via Icon Composer when installed)
Geometry is in fractions of the icon square (the design approved on 2026-11-12).
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
ICON_DOC = ASSETS / "AppIcon.icon"
BUILD = ASSETS / "build"
WEB = ROOT / "ailurus_tone" / "web"
ICTOOL = Path("/Applications/Xcode.app/Contents/Applications/Icon Composer.app/Contents/Executables/ictool")
SIZE, SS = 1024, 4
C = {"tile": "#b5532e", "tile_dark": "#5c2616", "body": "#2b2a28", "knob": "#f7d8a6",
     "pointer": "#7f0000", "led": "#ff3b1f", "nut": "#8f8a82", "switch": "#e4e0d8", "ring": "#b9b4ab"}


def _srgb(hex_: str) -> str:
    r, g, b = (int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return f"extended-srgb:{r:.5f},{g:.5f},{b:.5f},1.00000"


def _draw():
    t = SIZE * SS
    img = Image.new("RGBA", (t, t), (0, 0, 0, 0))

    def box(x, y, w, h):
        return [x * t, y * t, (x + w) * t, (y + h) * t]
    return img, ImageDraw.Draw(img), t, box


def layer_enclosure() -> Image.Image:
    img, d, t, box = _draw()
    d.rounded_rectangle(box(0.17, 0.05, 0.66, 0.90), radius=0.07 * t, fill=C["body"])
    return img.resize((SIZE, SIZE), Image.LANCZOS)


def layer_hardware() -> Image.Image:
    img, d, t, box = _draw()
    for left, angle in ((0.24, -45), (0.435, 10), (0.63, 50)):
        k = 0.13
        d.ellipse(box(left, 0.09, k, k), fill=C["knob"])
        cx, cy, r = (left + k / 2) * t, (0.09 + k / 2) * t, k / 2 * t
        a = math.radians(angle - 90)
        w = 0.12 * k * t
        p1 = (cx + math.cos(a) * r * 0.12, cy + math.sin(a) * r * 0.12)
        p2 = (cx + math.cos(a) * r * 0.88, cy + math.sin(a) * r * 0.88)
        d.line([p1, p2], fill=C["pointer"], width=round(w))
        for p in (p1, p2):
            d.ellipse([p[0] - w / 2, p[1] - w / 2, p[0] + w / 2, p[1] + w / 2], fill=C["pointer"])
    d.ellipse(box(0.4775, 0.615, 0.045, 0.045), fill=C["led"])
    x, y, n = 0.375, 0.67, 0.25
    hexagon = [(0.25, 0.03), (0.75, 0.03), (1, 0.5), (0.75, 0.97), (0.25, 0.97), (0, 0.5)]
    d.polygon([((x + px * n) * t, (y + py * n) * t) for px, py in hexagon], fill=C["nut"])
    d.ellipse(box(0.41, 0.705, 0.18, 0.18), fill=C["ring"])
    d.ellipse(box(0.423, 0.718, 0.154, 0.154), fill=C["switch"])
    return img.resize((SIZE, SIZE), Image.LANCZOS)


def layer_panda() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    logo = Image.open(ASSETS / "panda-logo.png").convert("RGBA")
    lw = round(0.36 * SIZE)
    logo = logo.resize((lw, round(lw * logo.height / logo.width)), Image.LANCZOS)
    img.alpha_composite(logo, (round(0.32 * SIZE), round(0.26 * SIZE)))
    return img


def write_icon_document():
    """One fill + one group of two layers. No glass on the artwork, so the panda keeps its
    exact colours; the system still adds the shape, edge highlight and shadow."""
    (ICON_DOC / "Assets").mkdir(parents=True, exist_ok=True)
    for old in (ICON_DOC / "Assets").glob("*.png"):
        old.unlink()
    layer_enclosure().save(ICON_DOC / "Assets" / "enclosure.png")
    layer_hardware().save(ICON_DOC / "Assets" / "hardware.png")
    layer_panda().save(ICON_DOC / "Assets" / "panda.png")
    light = {"automatic-gradient": _srgb(C["tile"])}
    dark = {"automatic-gradient": _srgb(C["tile_dark"])}
    doc = {
        "fill": light,
        "fill-specializations": [{"value": light}, {"appearance": "dark", "value": dark}],
        "groups": [{
            "layers": [  # first = top
                {"image-name": "panda.png", "name": "panda", "glass": False},
                {"image-name": "hardware.png", "name": "hardware", "glass": False},
                {"image-name": "enclosure.png", "name": "enclosure", "glass": False,
                 "fill-specializations": [{"value": {"solid": _srgb(C["body"])}},
                                          {"appearance": "dark", "value": {"solid": _srgb(C["tile"])}}]},
            ],
            "shadow": {"kind": "neutral", "opacity": 0.5},
            "translucency": {"enabled": False, "value": 0.5},
        }],
        "supported-platforms": {"squares": "shared"},
    }
    (ICON_DOC / "icon.json").write_text(json.dumps(doc, indent=2) + "\n")


def compile_icon() -> bool:
    if subprocess.run(["xcrun", "--find", "actool"], capture_output=True).returncode:
        print("actool not found (needs Xcode): skipping Assets.car / AppIcon.icns")
        return False
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["xcrun", "actool", str(ICON_DOC), "--compile", tmp, "--platform", "macosx",
                        "--minimum-deployment-target", "13.0", "--app-icon", "AppIcon",
                        "--target-device", "mac", "--output-partial-info-plist", f"{tmp}/partial.plist"],
                       check=True, capture_output=True)
        for name in ("Assets.car", "AppIcon.icns"):
            shutil.copy(Path(tmp) / name, BUILD / name)
    shutil.copy(BUILD / "AppIcon.icns", WEB / "AppIcon.icns")
    return True


def apple_render(rendition: str, out: Path) -> bool:
    """Render one look (Default, Dark, ClearLight, TintedDark, ...) the way macOS draws it."""
    if not ICTOOL.is_file():
        return False
    r = subprocess.run([str(ICTOOL), str(ICON_DOC), "--export-image", "--output-file", str(out),
                        "--platform", "macOS", "--rendition", rendition, "--width", "1024",
                        "--height", "1024", "--scale", "1"], capture_output=True)
    return r.returncode == 0 and out.is_file()


def flat_render() -> Image.Image:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "icon.png"
        if apple_render("Default", out):
            return Image.open(out).convert("RGBA")
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))   # fallback without Xcode
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=0.225 * SIZE, fill=255)
    img.paste(Image.new("RGBA", (SIZE, SIZE), C["tile"]), (0, 0), mask)
    img.alpha_composite(layer_enclosure())
    img.alpha_composite(layer_hardware())
    img.alpha_composite(layer_panda())
    return img


def main():
    BUILD.mkdir(parents=True, exist_ok=True)
    write_icon_document()
    compiled = compile_icon()
    flat = flat_render()
    flat.save(BUILD / "icon-1024.png")
    flat.save(BUILD / "icon.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    flat.resize((256, 256), Image.LANCZOS).save(WEB / "icon.png")
    print("icon document:", ICON_DOC.relative_to(ROOT), "| compiled:", compiled,
          "| outputs:", *sorted(p.name for p in BUILD.iterdir()))


if __name__ == "__main__":
    main()
