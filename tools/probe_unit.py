"""First look at a BOSS unit that Ailurus Tone Editor does not support yet. READ-ONLY.

Step 1, no unit needed: what its BOSS TONE STUDIO says about it.

    .venv/bin/python tools/probe_unit.py --bts "/Applications/BOSS/<unit>"

    prints the product name, BTS version, the SysEx header (model id), the unit's memory map
    (top-level areas such as the current sound and the stored patches) and the blocks of each
    area, all read from config/product_setting.js and config/address_map.js.

Step 2, unit plugged in over USB, BOSS TONE STUDIO NOT running:

    .venv/bin/python tools/probe_unit.py --ports
    .venv/bin/python tools/probe_unit.py --bts ... --port "<MIDI port>"
    .venv/bin/python tools/probe_unit.py --bts ... --port "<MIDI port>" --read 20000000 16

    --port sends the MIDI Universal Identity Request and prints the reply; --read sends ONE
    Roland RQ1 (a read request) for <size> bytes at <address> (4 raw bytes in hex, as in
    address_map.js) and prints what comes back, e.g. a patch name.

This tool never sends DT1 (a write) and never switches the unit into editor mode, so it cannot
change a sound or a stored patch.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ailurus_tone.locate import BTSError  # noqa: E402
from ailurus_tone.rolandbts import addr_bytes, parse_address_map, read_product  # noqa: E402

IDENTITY_REQUEST = [0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7]


def find_config(folder: Path) -> Path:
    """The config/ folder of a BTS install: the one holding product_setting.js + address_map.js."""
    for f in sorted(folder.rglob("product_setting.js"), key=lambda p: len(p.parts)):
        if (f.parent / "address_map.js").is_file():
            return f.parent
    raise BTSError(f"No config/product_setting.js with an address_map.js next to it below {folder}")


def show_bts(folder: Path):
    cfg = find_config(folder)
    p = read_product(cfg / "product_setting.js")
    print(f"tables      {cfg}")
    print(f"product     {p.name!r}  (BTS {p.version}, communicationLevel {p.communication_level})")
    print(f"SysEx       {bytes(p.header).hex(' ').upper()}   (F0, Roland 41, device id, model id)")
    amap = parse_address_map((cfg / "address_map.js").read_text(encoding="utf-8-sig"))
    print(f"tables      {len(amap.tables)} parameter tables")
    print("\nmemory map (root):")
    seen: set[str] = set()
    for off, child, name in amap.children.get("root", []):
        print(f"  {bytes(off).hex(' ').upper()}  {name:<24} -> {child}")
        if child in amap.children and child not in seen:
            seen.add(child)
    for child in sorted(seen):
        blocks = amap.blocks(child)
        print(f"\n{child}: {len(blocks)} blocks")
        for name, (off, table, size) in blocks.items():
            print(f"  +{bytes(off).hex(' ').upper()}  {name:<24} {table:<28} {size:>4} bytes")
    return p


def list_ports():
    import rtmidi
    print("MIDI in: ", rtmidi.MidiIn().get_ports())
    print("MIDI out:", rtmidi.MidiOut().get_ports())


def identity(port: str):
    import rtmidi
    mi, mo = rtmidi.MidiIn(), rtmidi.MidiOut()
    if port not in mi.get_ports() or port not in mo.get_ports():
        sys.exit(f"MIDI port {port!r} not found; --ports lists them")
    replies = []
    mi.open_port(mi.get_ports().index(port))
    mi.ignore_types(sysex=False, timing=True, active_sense=True)
    mi.set_callback(lambda ev, _=None: replies.append(ev[0]))
    mo.open_port(mo.get_ports().index(port))
    mo.send_message(IDENTITY_REQUEST)
    time.sleep(0.5)
    mi.close_port()
    mo.close_port()
    for r in replies:
        print("identity   ", bytes(r).hex(" ").upper())
    if not replies:
        print("identity    no reply (wrong port, BOSS TONE STUDIO running, or a hub in between?)")


def read(port: str, header: list[int], addr: str, size: int):
    from ailurus_tone.roland import RolandPort
    p = RolandPort(port, header)
    p.open()
    try:
        data = p.rq1(addr_bytes(addr.rjust(8, "0")), size)
    finally:
        p.close()
    print("read       ", bytes(data).hex(" ").upper())
    print("as text    ", bytes(x if 32 <= x < 127 else 46 for x in data).decode())


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bts", type=Path, help="folder that contains the unit's BOSS TONE STUDIO")
    ap.add_argument("--ports", action="store_true", help="list MIDI ports")
    ap.add_argument("--port", help="MIDI port of the unit (identity request)")
    ap.add_argument("--read", nargs=2, metavar=("ADDR", "SIZE"), help="one RQ1 read, e.g. 20000000 16")
    a = ap.parse_args()
    if a.ports:
        list_ports()
    product = show_bts(a.bts) if a.bts else None
    if a.port:
        identity(a.port)
    if a.read:
        if not (a.port and product):
            sys.exit("--read needs --bts (for the SysEx header) and --port")
        read(a.port, product.header, a.read[0], int(a.read[1]))
    if not (a.ports or a.bts or a.port):
        ap.print_help()


if __name__ == "__main__":
    main()
