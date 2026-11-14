"""Saving patches (unit.save / put_back) against a simulated unit: a port that keeps the unit's
memory, takes DT1 writes and performs the WRITE command the way the unit does. Needs the
installed BOSS TONE STUDIO for the tables (skipped otherwise)."""
import json

import pytest

import ailurus_tone.unit as unit_mod
import ailurus_tone.units.gx10 as gx_mod
import ailurus_tone.units.katana3 as kt_mod
from ailurus_tone.locate import BTSError
from ailurus_tone.roland import from7
from ailurus_tone.units.gx10 import BLOCKS, GX10, TEMP, nibbles4, user_base
from ailurus_tone.units.gx10_bts import Tables, find_bts_root
from ailurus_tone.units.katana3 import EDIT_BUFFER, EXPORT, PATCH_WRITE, PREVIEW_MUTE, Katana, patch_base
from ailurus_tone.units.katana3_bts import KatanaTables, find_katana_root


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    for m in (unit_mod, gx_mod, kt_mod):
        monkeypatch.setattr(m.time, "sleep", lambda s: None)


class SimPort:
    """The unit's memory by linear address. `on_write(addr, data)` plays the WRITE command."""
    connected = True

    def __init__(self, on_write):
        self.mem: dict[int, int] = {}
        self.sent = []
        self.on_write = on_write

    def poke(self, addr, data):
        for i, x in enumerate(data):
            self.mem[from7(addr) + i] = x

    def rq1(self, addr, size, timeout=0.0):
        a = from7(addr)
        return [self.mem.get(a + i, 0) for i in range(size)]

    def dt1(self, addr, data, pause=0.0):
        self.sent.append((list(addr), list(data)))
        self.poke(addr, data)

    def dt1_ack(self, addr, data, timeout=0.0):
        self.sent.append((list(addr), list(data)))
        self.on_write(list(addr), list(data))
        return list(data)

    def copy(self, src, dst, length):
        s, d = from7(src), from7(dst)
        for i in range(length):
            self.mem[d + i] = self.mem.get(s + i, 0)


# ---- GX-10 ----------------------------------------------------------------------------
@pytest.fixture(scope="module")
def gx_tables():
    try:
        t = Tables(find_bts_root())
    except BTSError:
        pytest.skip("BOSS TONE STUDIO not installed")
    yield t
    t.close()


def make_gx(tables):
    gx = GX10(tables)
    span = from7(user_base(1)) - from7(user_base(0))

    def on_write(addr, data):
        if addr == gx_mod.PATCH_WRITE:
            n = data[0] << 7 | data[1]
            gx.port.copy(TEMP, user_base(n), span)
            gx.port.poke(gx_mod.CURRENT_PATCH, nibbles4(n))

    gx.port = SimPort(on_write)
    for n, name in ((4, "OLD FOUR"), (5, "OLD FIVE")):
        gx.port.poke(user_base(n), [ord(c) for c in name.ljust(16)])
        gx.port.poke(gx_mod.add7(user_base(n), from7(BLOCKS[-1][1]) + 5), [n])   # some fx byte
    gx.port.poke(TEMP, [ord(c) for c in "MY NEW SOUND".ljust(16)])
    gx.port.poke(gx_mod.add7(TEMP, from7(BLOCKS[-1][1]) + 5), [99])
    gx.port.poke(gx_mod.CURRENT_PATCH, nibbles4(4))
    gx.names = gx.read_names()
    gx.reload()
    return gx


def test_gx_save_backs_up_writes_and_checks(gx_tables, tmp_path):
    gx = make_gx(gx_tables)
    r = gx.save(5, name="Lead", backup_dir=str(tmp_path))
    assert r["slot"] == "U02-3" and r["name"] == "Lead"
    assert ([0x7F, 0, 1, 4], [0, 5]) in gx.port.sent
    assert gx.read_saved(5)[0][:16] == [ord(c) for c in "Lead".ljust(16)]
    assert gx.read_saved(5)[-1][5] == 99
    assert gx.names[5] == "Lead" and gx.current == 5 and not gx.edited
    old = json.loads(open(r["backup"]).read())
    assert old["device"] == "GX-10" and old["patches"][0]["slot"] == 5
    assert old["patches"][0]["name"] == "OLD FIVE" and old["patches"][0]["blocks"][-1][5] == 5
    assert gx.log.events[-1]["action"] == "save"


def test_gx_never_saves_to_hidden_slots(gx_tables, tmp_path):
    gx = make_gx(gx_tables)
    for n in (198, 199, -1):
        with pytest.raises(ValueError):
            gx.save(n, backup_dir=str(tmp_path))
    assert not any(a == [0x7F, 0, 1, 4] for a, _ in gx.port.sent)
    assert not list(tmp_path.rglob("*.json"))


def test_gx_put_back_restores_the_overwritten_patch(gx_tables, tmp_path):
    gx = make_gx(gx_tables)
    r = gx.save(5, name="Lead", backup_dir=str(tmp_path))
    back = gx.put_back(r["backup"], backup_dir=str(tmp_path))
    assert back["name"] == "OLD FIVE"
    assert gx.read_saved(5)[-1][5] == 5
    assert len(list((tmp_path / "Overwritten").glob("*.json"))) == 2   # "Lead" was backed up too


# ---- KATANA Gen 3 ---------------------------------------------------------------------
@pytest.fixture(scope="module")
def kt_tables():
    try:
        t = KatanaTables(find_katana_root())
    except BTSError:
        pytest.skip("BOSS TONE STUDIO for KATANA Gen 3 not installed")
    yield t
    t.close()


def make_katana(tables):
    kt = Katana(tables)
    span = from7(patch_base(0)) - from7(EDIT_BUFFER)

    def on_write(addr, data):
        if addr == PATCH_WRITE:
            kt.port.copy(EDIT_BUFFER, patch_base(data[1]), span)
            kt.port.poke(kt_mod.SETUP, [0, data[1]])

    kt.port = SimPort(on_write)
    kt.port.poke(patch_base(3), [ord(c) for c in "OLD CH3".ljust(16)])
    kt.port.poke(EDIT_BUFFER, [ord(c) for c in "CRUNCH".ljust(16)])
    kt.port.poke(kt_mod.SETUP, [0, 3])
    kt.names = kt.read_names()
    kt.reload()
    return kt


def test_katana_save_follows_bts(kt_tables, tmp_path):
    kt = make_katana(kt_tables)
    r = kt.save(3, name="Clean", backup_dir=str(tmp_path))
    assert r["slot"] == kt.labels[3] and r["name"] == "Clean"
    cmds = [(a, d) for a, d in kt.port.sent if a[0] == 0x7F]
    assert cmds == [(PATCH_WRITE, [0, 3]), (PREVIEW_MUTE, [1]), (EXPORT, [0x40, 3])]
    assert kt.names[3] == "Clean"
    assert json.loads(open(r["backup"]).read())["patches"][0]["name"] == "OLD CH3"


def test_katana_never_saves_to_panel(kt_tables, tmp_path):
    kt = make_katana(kt_tables)
    with pytest.raises(ValueError, match="cannot be saved"):
        kt.save(0, backup_dir=str(tmp_path))
    assert not any(a == PATCH_WRITE for a, _ in kt.port.sent)


# ---- the AI may save only when the player allowed it ------------------------------------
def test_ai_save_needs_the_setting(gx_tables, tmp_path, monkeypatch):
    import ailurus_tone.state as state_mod
    gx = make_gx(gx_tables)
    s = object.__new__(state_mod.State)
    s.need = lambda device=None: gx
    s.bump = lambda *_: None
    cfg = {"backup_dir": str(tmp_path), "ai_can_save": False}
    monkeypatch.setattr(state_mod, "load_config", lambda: dict(cfg))
    with pytest.raises(PermissionError):
        s.save_patch(5, "AI", who="ai")
    assert not any(a == [0x7F, 0, 1, 4] for a, _ in gx.port.sent)
    cfg["ai_can_save"] = True
    import ailurus_tone.backup as backup_mod
    monkeypatch.setattr(backup_mod, "load_config", lambda: dict(cfg))
    assert s.save_patch(5, "AI", who="ai")["name"] == "AI"
    assert s.overwritten()[0]["name"] == "OLD FIVE"
    assert s.put_back(s.overwritten()[0]["file"])["name"] == "OLD FIVE"
