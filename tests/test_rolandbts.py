"""The shared BTS layer (rolandbts.py, unit.py, the unit registry) against both installed BTS.

The strongest check that the layer is really shared: the generic address_map.js parser must
reproduce the GX-10 patch layout that was worked out by hand on the unit."""
import pytest

from ailurus_tone.locate import BTSError
from ailurus_tone.rolandbts import decode, encode, parse_address_map, read_product
from ailurus_tone.units import BY_KIND, UNITS
from ailurus_tone.units.gx10 import BLOCKS, HEADER
from ailurus_tone.units.gx10_bts import find_bts_root
from ailurus_tone.units.katana3_bts import find_katana_root


def _root(find):
    try:
        return find()
    except BTSError:
        pytest.skip("BOSS TONE STUDIO not installed")


@pytest.fixture(scope="module")
def gx_config():
    return _root(find_bts_root) / "config"


@pytest.fixture(scope="module")
def katana_config():
    return _root(find_katana_root) / "js" / "config"


def test_values_roundtrip():
    for kind, n, ofs, v in [("byte", 1, 0, 77), ("byte", 1, 20, -20), ("byte7", 2, 0, 1000),
                            ("nibbles", 2, 0, 9), ("nibbles", 4, 0, 400), ("nibbles", 4, -1, 99)]:
        assert decode(encode(v, kind, n, ofs), kind, ofs) == v
    assert encode(400, "nibbles", 4) == [0, 1, 9, 0]
    with pytest.raises(ValueError):
        encode(1, "text", 16)


def test_product_settings(gx_config, katana_config):
    gx = read_product(gx_config / "product_setting.js")
    assert gx.name == "GX-10" and gx.header == HEADER
    k = read_product(katana_config / "product_setting.js")
    assert k.name == "KATANA Gen3" and k.header == [0xF0, 0x41, 0x10, 0x01, 0x05, 0x07]


def test_generic_map_reproduces_the_gx10_layout(gx_config):
    amap = parse_address_map((gx_config / "address_map.js").read_text(encoding="utf-8-sig"))
    found = {}
    for name, (off, _, size) in amap.blocks("Patch").items():   # 'assign(3)' -> assign3, 'fxItem(3)' -> fx3
        found[name.replace("fxItem", "fx").replace("(", "").replace(")", "")] = (off, size)
    for name, off, size in BLOCKS:
        assert found[name] == (off, size), name


def test_generic_map_reads_the_katana_memory_map(katana_config):
    amap = parse_address_map((katana_config / "address_map.js").read_text(encoding="utf-8-sig"))
    top = {name: off for off, _, name in amap.children["root"]}
    assert top["PATCH"] == [0x20, 0, 0, 0] and top["PATCH(1)"] == [0x20, 0x10, 0, 0]
    assert top["PATCH(8)"] == [0x21, 0, 0, 0] and top["COMMAND"] == [0x7F, 0, 0, 0]


def test_commented_out_tables_are_ignored():
    js = """
var A = [
\t{addr: 0x00000001,\tsize: INTEGER1x7,\tofs: 0,\tmin: 0,\tmax: 9,\tinit: 0,\tname: 'X'}, // note
];
// var B = [
// \t{addr: 0x00000000,\tsize: INTEGER1x7,\tofs: 0,\tmin: 0,\tmax: 1,\tinit: 0,\tname: 'Y'},
// ];
this.root = [
\t{addr: 0x10000000,\tsize: 0,\tchild: A,\tname: 'top'},
];
"""
    amap = parse_address_map(js)
    assert set(amap.tables) == {"A"}
    assert amap.tables["A"][0] == {"offset": 1, "kind": "byte", "n": 1, "ofs": 0, "min": 0, "max": 9,
                                   "id": "X", "init": 0}
    assert amap.children["root"] == [([0x10, 0, 0, 0], "A", "top")]


def test_registry():
    assert [u.kind for u in UNITS] == ["gx10", "katana3"]
    for u in UNITS:
        assert BY_KIND[u.kind] is u
        assert u.title and u.hint and u.port_name and u.bts and u.backup_prefix
