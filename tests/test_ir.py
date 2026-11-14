"""Cabinet IRs (ir.py and the GX-10's IR slots): WAV reading, the model fit, the DT1 encoding as
BTS sends it, and that a slot holding an IR is replaced only when the player confirmed."""
import json
import struct
import threading

import numpy as np
import pytest

import ailurus_tone.ir as ir
from ailurus_tone.history import ActivityLog
from ailurus_tone.roland import add7, from7
from ailurus_tone.units.gx10 import GX10


def wav(x, rate=48000, bits=24, channels=1, fmt_tag=None) -> bytes:
    x = np.repeat(np.asarray(x, float)[:, None], channels, axis=1).ravel()
    if bits == 16:
        body, tag = (np.clip(x, -1, 1 - 2 ** -15) * 32768).astype("<i2").tobytes(), 1
    elif bits == 24:
        v = (np.clip(x, -1, 1 - 2 ** -23) * (1 << 23)).astype(np.int32)
        body, tag = np.stack([v & 255, (v >> 8) & 255, (v >> 16) & 255], 1).astype(np.uint8).tobytes(), 1
    else:
        body, tag = x.astype("<f4").tobytes(), 3
    tag = fmt_tag or tag
    blk = channels * bits // 8
    fmt = struct.pack("<HHIIHH", tag, channels, rate, rate * blk, blk, bits)
    if tag == 0xFFFE:
        fmt += struct.pack("<HHIH", 22, bits, 0, 3 if bits == 32 else 1) + bytes(14)
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"LIST" + struct.pack("<I", 3) + b"abc\0"
    chunks += b"data" + struct.pack("<I", len(body)) + body
    return b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks


def cab_like(rate=48000) -> np.ndarray:
    """A made-up cabinet: low cut, a few resonances, high roll-off (filtered noise burst)."""
    rng = np.random.default_rng(1)
    n = rate // 10
    x = rng.standard_normal(n) * np.exp(-np.arange(n) / (rate * 0.004))
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1 / rate)
    shape = (f / 90) ** 2 / (1 + (f / 90) ** 2) / (1 + (f / 5000) ** 4) * (1 + 1.5 * np.exp(-((f - 2500) / 600) ** 2))
    y = np.fft.irfft(X * shape, n)
    return 0.8 * y / np.abs(y).max()


def test_read_wav_formats():
    x = np.sin(np.arange(480) / 10) * 0.5
    for bits, tag in ((16, None), (24, None), (32, None), (24, 0xFFFE), (32, 0xFFFE)):
        y, rate = ir.read_wav(wav(x, 44100, bits, channels=2, fmt_tag=tag))
        assert rate == 44100 and len(y) == 480
        assert np.abs(y - x).max() < 1e-3
    with pytest.raises(ir.IRError):
        ir.read_wav(b"RIFF....WAVEfmt ")
    with pytest.raises(ir.IRError, match="silent"):
        ir.read_wav(wav(np.zeros(100)))


def test_dirac_gives_a_flat_model_at_the_reference_level():
    for rate in (44100, 48000, 96000):
        d = np.zeros(rate // 10)
        d[0] = 0.9
        r = ir.convert(wav(d, rate))
        assert r["error_db"] < 0.3
        bqs = [r["words"][5 * i:5 * i + 5] for i in range(7)]
        resp = ir.biquads_db(bqs) + 20 * np.log10(r["words"][55])
        band = (ir.GRID > 100) & (ir.GRID < 10000)
        assert np.abs(resp[band] - ir.LEVEL_DB).max() < 0.8


def test_cabinet_fit_and_level():
    r = ir.convert(wav(cab_like()))
    assert len(r["words"]) == ir.NUM_WORDS and r["error_db"] < 2.5
    w = r["words"]
    bqs = [w[5 * i:5 * i + 5] for i in range(7)]
    resp = ir.biquads_db(bqs) + 20 * np.log10(w[55])
    mid = (ir.GRID >= 200) & (ir.GRID <= 4000)
    assert abs(resp[mid].mean() - ir.LEVEL_DB) < 0.01
    for k in range(4):                      # resonant groups unused, taps a single direct path
        assert w[35 + 5 * k:40 + 5 * k] == ir.NEUTRAL_GROUP
    assert w[56:87] == [0.0] * 31 and w[87] == 0.0
    for _b0, _b1, _b2, c1, c2 in bqs:       # every biquad stable
        assert np.abs(np.roots([1, -c1, -c2])).max() < 1


def test_word_messages_follow_bts():
    words = [1.0, -2.5] + [0.0] * 117
    msgs = ir.word_messages(words)
    assert [m[0] for m in msgs] == [0x10 + k * 240 for k in range(5)]
    assert [len(m[1]) for m in msgs] == [240, 240, 240, 240, 64]      # padded to 128 words
    assert msgs[0][1][:8] == [3, 0xF, 8, 0, 0, 0, 0, 0]               # 1.0 = 3F800000
    assert msgs[0][1][8:16] == [0xC, 0, 2, 0, 0, 0, 0, 0]             # -2.5 = C0200000
    assert msgs[-1][1][-8:] == [0, 0, 0, 0, 0, 0, 2, 0]               # padding word 0x20
    assert all(0 <= v < 16 for _, d in msgs for v in d)


# ---- the GX-10's IR slots against a simulated port ----------------------------------------
class IRPort:
    connected = True

    def __init__(self):
        self.mem: dict[int, int] = {}
        self.sent = []
        for n in range(16):
            self.poke(ir.slot_base(n), [ord(c) for c in f"USER {n + 1}".ljust(12)])
        self.poke(ir.slot_base(0), [ord(c) for c in "V30 3 Livepl"])

    def poke(self, addr, data):
        for i, x in enumerate(data):
            self.mem[from7(addr) + i] = x

    def rq1(self, addr, size, timeout=0.0):
        return [self.mem.get(from7(addr) + i, 0) for i in range(size)]

    def dt1(self, addr, data, pause=0.0):
        self.sent.append((list(addr), list(data)))
        self.poke(addr, data)


@pytest.fixture
def gx(tmp_path, monkeypatch):
    monkeypatch.setattr(ir.time, "sleep", lambda s: None)
    g = object.__new__(GX10)
    g.lock = threading.RLock()
    g.port = IRPort()
    g.log = ActivityLog(None)
    g.on_event = lambda kind, data: None
    return g


def test_ir_names_and_empty_slots(gx):
    names = gx.ir_names()
    assert names[0] == "V30 3 Livepl" and names[15] == "USER 16"
    assert not gx.ir_slot_empty(0, names[0]) and gx.ir_slot_empty(15, names[15])


def test_load_ir_writes_name_and_data(gx):
    words = ir.convert(wav(cab_like()))["words"]
    r = gx.load_ir(15, "My Cab", words)
    assert r["name"] == "My Cab" and gx.ir_names()[15] == "My Cab"
    base = ir.slot_base(15)
    assert gx.port.sent[0] == (base, [ord(c) for c in "My Cab".ljust(12)])
    first = gx.port.sent[1]
    assert first[0] == add7(base, 0x10)
    got = int("".join(f"{v:x}" for v in first[1][:8]), 16)
    assert struct.unpack("<f", struct.pack("<I", got))[0] == pytest.approx(words[0], rel=1e-6)
    assert all(a[1] == 0x40 + 15 for a, _ in gx.port.sent)          # nothing outside USER 16
    assert gx.log.events[-1]["action"] == "ir_load"


def test_a_slot_holding_an_ir_needs_confirmation(gx):
    words = ir.convert(wav(cab_like()))["words"]
    with pytest.raises(ValueError, match="cannot be backed up"):
        gx.load_ir(0, "Other", words)
    assert not gx.port.sent
    gx.load_ir(0, "Other", words, replace=True)
    assert gx.ir_names()[0] == "Other"


def test_rename_and_bad_slots(gx):
    gx.rename_ir(15, "Greenback")
    assert gx.ir_names()[15] == "Greenback"
    for bad in (-1, 16):
        with pytest.raises(ValueError):
            gx.rename_ir(bad, "x")
    with pytest.raises(ValueError):
        gx.load_ir(3, "", [0.0] * 119)
    with pytest.raises(ValueError):
        gx.load_ir(3, "short", [0.0] * 10)


def test_state_keeps_each_loaded_ir(gx, tmp_path, monkeypatch):
    import ailurus_tone.state as state_mod
    s = object.__new__(state_mod.State)
    s.need = lambda device=None: gx
    s.bump = lambda *_: None
    monkeypatch.setattr(state_mod, "load_config", lambda: {"backup_dir": str(tmp_path)})
    r = s.ir_load(15, wav(cab_like()), "Greenback 4x12 SM57.wav")
    assert r["name"] == "Greenback 4x" and r["error_db"] < 2.5
    rec = json.loads(open(r["file"]).read())
    assert rec["slot"] == 15 and rec["source"] == "Greenback 4x12 SM57.wav" and len(rec["words"]) == 119
    slots = s.ir_slots()["slots"]
    assert slots[15]["source"] == "Greenback 4x12 SM57.wav" and not slots[15]["empty"]
    assert slots[0]["source"] is None and slots[3]["empty"]
