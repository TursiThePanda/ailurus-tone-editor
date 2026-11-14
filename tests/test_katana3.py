"""KATANA Gen 3 profile without the amp: tables from the installed BTS, a fake port that answers
every read with the BTS init values (or zeros). Skipped when BTS for KATANA Gen 3 is not installed."""
import pytest

from ailurus_tone.locate import BTSError
from ailurus_tone.roland import from7
from ailurus_tone.units.katana3 import EDIT_BUFFER, NUM_PATCHES, SECTIONS, Katana, decode, patch_base
from ailurus_tone.units.katana3_bts import KatanaTables, find_katana_root


@pytest.fixture(scope="module")
def tables():
    try:
        t = KatanaTables(find_katana_root())
    except BTSError:
        pytest.skip("BOSS TONE STUDIO for KATANA Gen 3 not installed")
    yield t
    t.close()


class ZeroPort:
    connected = True

    def __init__(self, overrides=None):
        self.sent = []
        self.overrides = overrides or {}   # linear address -> bytes

    def rq1(self, addr, size, timeout=0.0):
        a = from7(addr)
        return list(self.overrides.get(a, [0] * size))[:size] + [0] * max(0, size - len(self.overrides.get(a, [])))

    def dt1(self, addr, data, pause=0.0):
        self.sent.append((list(addr), list(data)))

    def open(self):
        pass

    def close(self):
        pass


def make(tables, overrides=None):
    k = Katana(tables)
    k.port = ZeroPort(overrides)
    k.names = k.read_names()
    k.reload()
    return k


def test_header_and_labels(tables):
    assert tables.header == [0xF0, 0x41, 0x10, 0x01, 0x05, 0x07]
    k = Katana(tables)
    assert k.labels == ["PANEL", "A:CH1", "A:CH2", "A:CH3", "A:CH4", "B:CH1", "B:CH2", "B:CH3", "B:CH4"]
    assert k.parse_slot("a:ch1") == 1 and k.parse_slot("PANEL") == 0
    with pytest.raises(ValueError):
        k.parse_slot("U01-1")


def test_patch_addresses():
    # PATCH(1) = PANEL at 20 10 00 00 ... PATCH(8) jumps to 21 00 00 00 (7-bit address space)
    assert patch_base(0) == [0x20, 0x10, 0, 0]
    assert patch_base(6) == [0x20, 0x70, 0, 0]
    assert patch_base(7) == [0x21, 0x00, 0, 0]
    assert patch_base(NUM_PATCHES - 1) == [0x21, 0x10, 0, 0]


def test_tables_from_bts(tables):
    assert len(tables.fx_groups) == len(tables.fx_types) == 31
    assert tables.fx_groups[0] == "TWAH" and tables.fx_types[0] == "T.WAH"
    delay = {p["name"]: p for p in tables.params("DELAY(1)")}
    assert tables.display(delay["HIGH CUT"], 14) == "FLAT"
    assert tables.display(delay["TIME"], 400).replace(" ", "") == "400ms"
    reverb = {p["name"]: p for p in tables.params("REVERB(1)")}
    assert reverb["REVERB TIME"]["ofs"] == -1      # stored value = shown value - 1


def test_every_section_decodes(tables):
    k = make(tables)
    s = k.summary()
    assert sorted(b["type"] for b in s["chain"]) == sorted(x[0] for x in SECTIONS)
    assert not s["readonly"] and not s["straight"]
    for b in s["chain"]:
        d = k.describe_fx(b["fx"])
        names = [p["name"] for p in d["params"]]
        assert len(names) == len(set(names)), f"duplicate names in {d['type']}"
        for p in d["params"]:
            assert p["min"] <= p["value"] <= p["max"] or p["value"] < p["min"]  # zeros may sit below min


def test_variation_picks_the_block(tables):
    """COLOR says which of the three variations plays: BOOSTER COLOR = RED -> BOOSTER(2)."""
    color = from7([0x20, 0, 0x04, 0])
    booster2 = from7([0x20, 0, 0x0C, 0])
    k = make(tables, {color: [1, 0, 0, 0, 0], booster2: [11, 77]})   # BOOSTER(2): T-SCREAM, DRIVE 77
    d = k.describe_fx(2)
    vals = {p["name"]: p for p in d["params"]}
    assert vals["VARIATION"]["display"] == "RED" and d["color"] == "red"
    assert d["sub"] == "T-SCREAM"
    assert vals["DRIVE"]["value"] == 77


def test_fx_type_filters_params(tables):
    fx1 = from7([0x20, 0, 0x10, 0])
    k = make(tables, {fx1: [3]})          # MOD = FX(1): type 3 = COMP
    d = k.describe_fx(3)
    assert d["sub"] == "COMP"
    names = {p["name"] for p in d["params"]}
    assert "SUSTAIN" in names and "SENS" not in names


def test_edits_go_to_the_edit_buffer(tables):
    """A change writes PATCH (20 00 ..) only, never a memory (20 10 .. and up) and never PATCH_WRITE."""
    k = make(tables)
    r = k.set_param(6, "FEEDBACK", 40)             # DELAY (GREEN) = DELAY(1) at 20 00 28 00, FEEDBACK at +5
    assert r["new"] == 40
    assert k.port.sent == [([0x20, 0, 0x28, 5], [40])]
    k.set_param(6, "TIME", 400)                    # INTEGER4x4 at +1: four nibbles
    assert k.port.sent[-1] == ([0x20, 0, 0x28, 1], [0, 1, 9, 0])
    k.set_switch(6, True)
    assert k.port.sent[-1] == ([0x20, 0, 0x08, 3], [1])
    for a, _ in k.port.sent:
        assert a[0] == 0x20 and a[1] == 0, a
    k.set_param(4, "AMP TYPE", "LEAD")
    assert k.describe_fx(4)["sub"] == "LEAD" and k._value(k.temp, "AMP", 7) == 4


def test_undo_redo_and_history(tables):
    k = make(tables)
    k.set_param(2, "DRIVE", 77)
    k.set_param(2, "VARIATION", "RED")             # now BOOSTER(2) is shown; undo must still hit BOOSTER(1)
    assert k.summary()["can_undo"]
    k.undo()
    k.undo()
    assert k._value(k.temp, "BOOSTER(1)", 1) == 0 and k._value(k.temp, "COLOR", 0) == 0
    k.redo()
    assert k._value(k.temp, "BOOSTER(1)", 1) == 77


def test_bad_values_are_refused(tables):
    k = make(tables)
    with pytest.raises(ValueError):
        k.set_param(6, "FEEDBACK", 101)
    with pytest.raises(ValueError):
        k.set_param(2, "VARIATION", "BLUE")
    with pytest.raises(KeyError):
        k.set_param(2, "NOPE", 1)
    with pytest.raises(ValueError):
        k.set_chain([1, 2])
    assert k.port.sent == []


def test_select_patch(tables):
    k = make(tables)
    k.select_patch(1)
    assert k.port.sent[0] == ([0x7F, 0, 1, 0], [0, 1])
    with pytest.raises(ValueError):
        k.select_patch(9)                          # PATCH(10) = PREVIEW is not selectable


def test_chain_follows_chain_parameter(tables):
    other = from7([0x20, 0, 0x02, 0])
    k = make(tables, {other: [2, 0, 0]})          # CHAIN 2: pdl bst mod fx amp ...
    order = [SECTIONS[i][0] for i in k.order()]
    assert order.index("FX") < order.index("AMP") < order.index("DELAY")
    amp = {p["name"]: p for p in k.describe_fx(4)["params"]}
    assert amp["CHAIN"]["display"].startswith("PEDAL > BST > MOD > FX > AMP")


def test_own_patch_select_echo_is_not_logged(tables):
    k = make(tables)
    k.select_patch(0)
    before = k.log.seq
    k._unsolicited([0, 0, 0, 0], [0, 0])           # the amp confirms the patch we selected
    assert k.log.seq == before


def test_unit_knob_is_taken_over(tables):
    k = make(tables)
    k._unsolicited([0x20, 0, 0x06, 0], [88])        # GAIN turned on the amp
    assert k._value(k.temp, "AMP", 0) == 88
    assert k.port.sent == []


def test_decode_sizes(tables):
    delay = {p["name"]: p for p in tables.params("DELAY(1)")}
    t = delay["TIME"]                      # INTEGER4x4: 400 ms = nibbles 0 1 9 0
    blk = [0] * 20
    blk[t["offset"]:t["offset"] + 4] = [0, 1, 9, 0]
    assert decode(blk, t) == 400


def test_edit_buffer_is_not_a_slot():
    assert EDIT_BUFFER == [0x20, 0, 0, 0] and EDIT_BUFFER not in [patch_base(n) for n in range(NUM_PATCHES)]
