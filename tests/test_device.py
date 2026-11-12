"""Unit tests that need no GX-10: encoding, chain walk, live edits and undo against a fake port.

They need the installed BOSS TONE STUDIO for the tables (skipped otherwise).
"""
import pytest

from ailurus_tone.bts import BTSError, Tables, find_bts_root
from ailurus_tone.device import BLOCK_INDEX, BLOCKS, GX10, encode_value, read_value, slot_label, user_base
from ailurus_tone.mcp_tools import parse_slot
from ailurus_tone.roland import add7, checksum, from7, to7


@pytest.fixture(scope="module")
def tables():
    try:
        t = Tables(find_bts_root())
    except BTSError:
        pytest.skip("BOSS TONE STUDIO not installed")
    yield t
    t.close()


class FakePort:
    connected = True

    def __init__(self):
        self.sent = []

    def dt1(self, addr, data, pause=0.0):
        self.sent.append((list(addr), list(data)))

    def rq1(self, addr, size, timeout=0.0):
        """Echo the last write to this address, like a unit that took it."""
        for a, d in reversed(self.sent):
            if a == list(addr) and len(d) == size:
                return list(d)
        raise AssertionError(f"unexpected read {addr}")


def make_gx(tables, chain_types):
    gx = GX10(tables)
    gx.port = FakePort()
    temp = [[0] * size for _, _, size in BLOCKS]
    efct = temp[BLOCK_INDEX["efct"]]
    efct[0x0C] = 0 + 1                               # chain top = item 0, stored +1
    for i, t in enumerate(chain_types):
        temp[BLOCK_INDEX[f"fx{i + 1}"]][0] = tables.fx_types.index(t)
        temp[BLOCK_INDEX[f"fx{i + 1}"]][1] = 1
        for prm in tables.params(t):  # start from BTS init values, like a fresh patch
            init = str(prm.get("init", "")).lstrip("-")
            if init.isdigit() and prm["address"] > 1:
                v = encode_value(prm, int(prm["init"]))
                temp[BLOCK_INDEX[f"fx{i + 1}"]][prm["address"]:prm["address"] + len(v)] = v
        efct[0x0D + i] = (i + 1 if i + 1 < len(chain_types) else -1) + 1
    gx.temp, gx.saved, gx.current = temp, [list(b) for b in temp], 195
    gx.history[195] = {"undo": [], "redo": []}
    return gx


def test_7bit_helpers():
    assert to7(from7([0x29, 0x12, 0, 0])) == [0x29, 0x12, 0, 0]
    assert add7([0x20, 0, 0, 0], 195 * from7([6, 0, 0])) == [0x29, 0x12, 0, 0]   # U66-1, proven on the unit
    assert user_base(195) == [0x29, 0x12, 0, 0]
    assert checksum([0x7F, 0, 1, 4, 1, 0x43]) == (128 - (0x7F + 1 + 4 + 1 + 0x43) % 128) % 128


def test_slot_labels():
    assert slot_label(0) == "U01-1"
    assert slot_label(195) == "U66-1"
    assert slot_label(197) == "U66-3"
    assert parse_slot("U66-1") == 195
    with pytest.raises(ValueError):
        parse_slot("U67-1")


def test_encode_roundtrip(tables):
    p = next(p for p in tables.params("GRAPHIC EQUALIZER") if p["name"] == "4kHz")
    blk = [0] * 179
    blk[p["address"]:p["address"] + 4] = encode_value(p, 10)
    assert read_value(blk, p) == 10
    assert tables.display(p, 10) == "+10dB"


def test_chain_order(tables):
    gx = make_gx(tables, ["NOISE SUPPRESSOR", "AIRD PREAMP", "GRAPHIC EQUALIZER"])
    assert [c["type"] for c in gx.summary()["chain"]] == ["NOISE SUPPRESSOR", "AIRD PREAMP", "GRAPHIC EQUALIZER"]


def test_live_edit_undo_redo(tables):
    gx = make_gx(tables, ["GRAPHIC EQUALIZER"])
    r = gx.set_param(1, "4kHz", 10, who="ai")
    assert r["old"] == 0 and r["new"] == 10
    addr, data = gx.port.sent[-1]
    assert addr[0] == 0x10, "live edits must go to TEMP only"
    assert gx.edited and gx.summary()["can_undo"]
    gx.undo()
    assert read_value(gx.fx_block(gx.temp, 1), next(p for p in tables.params("GRAPHIC EQUALIZER")
                                                    if p["name"] == "4kHz")) == 0
    assert not gx.edited
    gx.redo()
    assert gx.summary()["changes"][0]["new"] == 10


def test_drag_is_one_history_entry(tables):
    gx = make_gx(tables, ["GRAPHIC EQUALIZER"])
    for v in (1, 2, 3, 4, 5):
        gx.set_param(1, "4kHz", v, record=False)
    gx.record_change(1, "4kHz", 0, 5)
    assert len(gx.history[195]["undo"]) == 1


def test_rejects_out_of_range_and_unknown(tables):
    gx = make_gx(tables, ["GRAPHIC EQUALIZER"])
    with pytest.raises(ValueError):
        gx.set_param(1, "4kHz", 999)
    with pytest.raises(KeyError):
        gx.set_param(1, "NO SUCH PARAM", 1)


def test_never_writes_user_slots(tables):
    gx = make_gx(tables, ["GRAPHIC EQUALIZER", "NOISE SUPPRESSOR"])
    gx.set_param(1, "2kHz", 4)
    gx.set_switch(2, False)
    gx.undo()
    assert all(a[0] == 0x10 for a, _ in gx.port.sent)


def test_history_logs_who_did_what(tables, tmp_path):
    from ailurus_tone.history import ActivityLog
    gx = make_gx(tables, ["GRAPHIC EQUALIZER"])
    gx.log = ActivityLog(tmp_path)
    gx.set_param(1, "4kHz", 3, who="ai")
    gx.undo(who="you")
    ev = gx.log.since(0)
    assert [(e["who"], e["action"], e["old"], e["new"]) for e in ev] == [("ai", "set", 0, 3), ("you", "undo", 3, 0)]
    assert ev[0]["patch"] == "U66-1" and ev[0]["type"] == "GRAPHIC EQUALIZER"
    gx.log.flush()
    reloaded = ActivityLog(tmp_path)          # the diary survives a restart
    assert [e["action"] for e in reloaded.since(0)] == ["set", "undo"]
    assert reloaded.seq == 2


def test_unit_knob_burst_is_one_event(tables):
    gx = make_gx(tables, ["GRAPHIC EQUALIZER"])
    p = next(p for p in tables.params("GRAPHIC EQUALIZER") if p["name"] == "4kHz")
    rel = from7(BLOCKS[BLOCK_INDEX["fx1"]][1]) + p["address"]
    for v in (1, 2, 3, 4):                     # knob turned on the unit: several messages
        gx.apply_unit_write(rel, encode_value(p, v))
    ev = gx.log.since(0)
    assert len(ev) == 1 and ev[0]["who"] == "unit" and (ev[0]["old"], ev[0]["new"]) == (0, 4)
    assert gx.describe_fx(1)["params"][[q["name"] for q in gx.describe_fx(1)["params"]].index("4kHz")]["value"] == 4


def test_reorder_straight_chain_with_undo(tables):
    gx = make_gx(tables, ["NOISE SUPPRESSOR", "COMPRESSOR", "OVERDRIVE"])
    assert gx.chain() == [1, 2, 3] and gx.is_straight()
    gx.set_chain([2, 1, 3])
    assert gx.chain() == [2, 1, 3]
    sent = gx.port.sent
    assert sent[0] == ([0, 0x20, 0, 3], [1]) and sent[-1] == ([0, 0x20, 0, 3], [0])   # BTS's edit bracket
    addr, data = sent[1]
    assert addr == [0x10, 0, 0x0F, 0x0C] and len(data) == 50 and data[0] == 2       # top = fx2 (item 1, +1)
    ev = gx.log.since(0)[-1]
    assert ev["param"] == "CHAIN" and ev["old"] == [1, 2, 3] and ev["new"] == [2, 1, 3]
    gx.undo()
    assert gx.chain() == [1, 2, 3]
    gx.redo()
    assert gx.chain() == [2, 1, 3]
    with pytest.raises(ValueError):
        gx.set_chain([1, 2])                    # must name exactly the fx on the chain


def test_reorder_refused_with_branches(tables):
    gx = make_gx(tables, ["NOISE SUPPRESSOR", "DIVIDER", "OVERDRIVE"])
    assert not gx.is_straight()
    with pytest.raises(ValueError):
        gx.set_chain([3, 2, 1])
    assert gx.port.sent == []
