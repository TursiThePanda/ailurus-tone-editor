"""Cabinet IRs for the GX-10's 16 user IR slots (AIRD PREAMP → SP TYPE = USER 1..16).

The GX-10 does not convolve an IR. A slot holds 119 float32 words, a MODEL of the cabinet
(decoded by measuring the unit over its USB audio, 2026-11-14):
- words 0..34   7 biquads [b0, b1, b2, c1, c2]: y = b0·x + b1·x1 + b2·x2 + c1·y1 + c2·y2, 48 kHz
- words 35..54  4 resonant boosts [g, f, -g, f', gain]: x + gain·bandpass (unity peak);
                not used here (gain 0)
- words 55..86  32 tap gains, words 87..118 their delays (samples at 48 kHz = value·16384)
BOSS TONE STUDIO fits that model with its own native code; convert() is our own fit: the WAV's
1/6-octave magnitude from 30 Hz to 20 kHz matched by 7 biquads (high-pass, low-pass, 5 peaking),
taps left as a single direct path that sets the level.

Transfer (BTS js/ir/ir_data_loader.js): the 12-character name by DT1 at 60 40 00 00 + slot·00 01
00 00, the words at +00 00 00 10, each word as 8 nibbles (high first), 30 words per message,
padded to 128 words. The unit cannot send an IR back, so a slot cannot be backed up.
"""
from __future__ import annotations

import struct
import time

import numpy as np

FS = 48000
NUM_SLOTS = 16
NAME_LEN = 12
NUM_WORDS = 119
GRID = np.geomspace(30, 20000, 240)          # fit frequencies (Hz)
LEVEL_DB = -3.2       # mean response 200 Hz..4 kHz, = BTS's own fit of a Mesa 4x12 IR on the unit
KINDS = ("hp", "lp", "pk", "pk", "pk", "pk", "pk")
START_F = (70, 6000, 120, 400, 1500, 3000, 8000)
NEUTRAL_GROUP = [0.81497, 0.0044218, -0.81497, 0.0034813, 0.0]   # as BTS writes an unused one


class IRError(ValueError):
    pass


# ---- WAV ---------------------------------------------------------------------------------
def read_wav(data: bytes) -> tuple[np.ndarray, int]:
    """Mono float samples and the sample rate of a RIFF WAV (PCM 16/24/32-bit or float 32/64)."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise IRError("Not a WAV file.")
    fmt = None
    pos = 12
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            tag, ch, rate, _, _, bits = struct.unpack("<HHIIHH", body[:16])
            if tag == 0xFFFE and len(body) >= 26:          # WAVE_FORMAT_EXTENSIBLE
                tag = struct.unpack("<H", body[24:26])[0]
            fmt = (tag, ch, rate, bits)
        elif cid == b"data":
            if fmt is None:
                raise IRError("The WAV has no format chunk before its data.")
            return _samples(body, *fmt), fmt[2]
        pos += 8 + size + (size & 1)
    raise IRError("The WAV has no audio data.")


def _samples(body: bytes, tag: int, ch: int, rate: int, bits: int) -> np.ndarray:
    if tag == 1 and bits == 16:
        x = np.frombuffer(body[:len(body) // 2 * 2], "<i2") / 32768.0
    elif tag == 1 and bits == 24:
        b = np.frombuffer(body[:len(body) // 3 * 3], np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        x = np.where(v >= 1 << 23, v - (1 << 24), v) / float(1 << 23)
    elif tag == 1 and bits == 32:
        x = np.frombuffer(body[:len(body) // 4 * 4], "<i4") / 2147483648.0
    elif tag == 3 and bits in (32, 64):
        x = np.frombuffer(body[:len(body) // (bits // 8) * (bits // 8)], "<f4" if bits == 32 else "<f8")
    else:
        raise IRError(f"Unsupported WAV format ({bits}-bit, format {tag}). Use 16/24-bit PCM or 32-bit float.")
    if ch < 1 or rate < 8000:
        raise IRError("The WAV header is not valid.")
    x = x[:len(x) // ch * ch].reshape(-1, ch).mean(axis=1)
    if not len(x) or not np.isfinite(x).all() or not np.abs(x).max():
        raise IRError("The WAV is silent.")
    return x.astype(float)


def prepare(x: np.ndarray, rate: int) -> np.ndarray:
    """48 kHz, starting just before the first strong sample, at most 0.5 s."""
    x = x[:int(rate * 0.6)]
    if rate != FS:                                   # band-limited resample in the frequency domain
        n = len(x)
        m = max(1, int(round(n * FS / rate)))
        X = np.fft.rfft(x, n)
        k = min(len(X), m // 2 + 1)
        Y = np.zeros(m // 2 + 1, complex)
        Y[:k] = X[:k]
        x = np.fft.irfft(Y, m) * (m / n)
    on = int(np.argmax(np.abs(x) > 0.05 * np.abs(x).max()))
    return x[max(0, on - 8):max(0, on - 8) + FS // 2]


# ---- the fit -----------------------------------------------------------------------------
def smooth_db(x: np.ndarray) -> np.ndarray:
    n = 1 << 16
    p = np.abs(np.fft.rfft(x, n)) ** 2
    f = np.fft.rfftfreq(n, 1 / FS)
    out = np.empty(len(GRID))
    for i, fc in enumerate(GRID):
        sel = (f >= fc * 2 ** (-1 / 12)) & (f <= fc * 2 ** (1 / 12))
        out[i] = p[sel].mean() if sel.any() else p[np.argmin(np.abs(f - fc))]
    return 10 * np.log10(out + 1e-20)


def rbj(kind: str, f0: float, q: float, gain_db: float = 0.0) -> list[float]:
    """Audio EQ Cookbook biquad as the unit takes it: [b0, b1, b2, c1, c2] with c = -a."""
    a_ = 10 ** (gain_db / 40)
    w = 2 * np.pi * min(f0, 0.45 * FS) / FS
    al, c = np.sin(w) / (2 * q), np.cos(w)
    if kind == "hp":
        b, a = [(1 + c) / 2, -(1 + c), (1 + c) / 2], [1 + al, -2 * c, 1 - al]
    elif kind == "lp":
        b, a = [(1 - c) / 2, 1 - c, (1 - c) / 2], [1 + al, -2 * c, 1 - al]
    else:
        b, a = [1 + al * a_, -2 * c, 1 - al * a_], [1 + al / a_, -2 * c, 1 - al / a_]
    return [b[0] / a[0], b[1] / a[0], b[2] / a[0], -a[1] / a[0], -a[2] / a[0]]


def biquads_db(bqs, f=GRID) -> np.ndarray:
    z1 = np.exp(-2j * np.pi * np.asarray(f) / FS)
    h = np.ones(len(z1), complex)
    for b0, b1, b2, c1, c2 in bqs:
        h *= (b0 + b1 * z1 + b2 * z1 ** 2) / (1 - c1 * z1 - c2 * z1 ** 2)
    return 20 * np.log10(np.abs(h) + 1e-12)


def _unpack(v: np.ndarray):
    """Unbounded fit variables -> biquads. f0 20 Hz..21 kHz, Q 0.3..8, peaks ±24 dB."""
    s = 1 / (1 + np.exp(-np.clip(v, -40, 40)))
    bqs = []
    for i, kind in enumerate(KINDS):
        f0 = 20 * (21000 / 20) ** s[3 * i]
        q = 0.3 * (8 / 0.3) ** s[3 * i + 1]
        g = 24 * np.tanh(v[3 * i + 2]) if kind == "pk" else 0.0
        bqs.append(rbj(kind, f0, q, g))
    return bqs, v[-1]


STARTS = ((70, 6000, 120, 400, 1500, 3000, 8000), (80, 5000, 200, 700, 2000, 3500, 9000),
          (60, 7000, 100, 300, 1000, 2500, 5000), (90, 4500, 150, 500, 1200, 2500, 4000))


def _start(freqs, q_peak: float) -> np.ndarray:
    def logit(p):
        return np.log(p / (1 - p))
    v = []
    for kind, f0 in zip(KINDS, freqs, strict=True):
        q = 0.7 if kind != "pk" else q_peak
        v += [logit(np.log(f0 / 20) / np.log(21000 / 20)), logit(np.log(q / 0.3) / np.log(8 / 0.3)), 0.0]
    return np.array(v + [0.0])


def _lm(res, v: np.ndarray, iterations: int = 300) -> tuple[np.ndarray, float]:
    """Levenberg-Marquardt with a numerical Jacobian."""
    r = res(v)
    lam = 1e-2
    eye = 1e-6 * np.eye(len(v))
    for _ in range(iterations):
        jac = np.empty((len(r), len(v)))
        for j in range(len(v)):
            d = np.zeros(len(v))
            d[j] = 1e-6
            jac[:, j] = (res(v + d) - r) / 1e-6
        jtj, jtr = jac.T @ jac, jac.T @ r
        for _ in range(12):
            step = np.linalg.solve(jtj + lam * (np.diag(np.diag(jtj)) + eye), -jtr)
            r2 = res(v + step)
            if r2 @ r2 < (r @ r) * (1 - 1e-9):
                v, r, lam = v + step, r2, max(lam / 3, 1e-9)
                break
            lam *= 4
        else:
            break
    return v, float(r @ r)


def fit(target_db: np.ndarray):
    """7 biquads + a level fitted to target_db: the best of 8 starts. -> (biquads, RMS error dB)."""
    w = np.where(GRID < 60, 0.5, 1.0)

    def res(v):
        bqs, lvl = _unpack(v)
        return w * (biquads_db(bqs) + lvl - target_db)

    best = None
    for freqs in STARTS:
        for q_peak in (1.0, 2.0):
            v = _start(freqs, q_peak)
            v[-1] = float(np.median(target_db))
            v, cost = _lm(res, v)
            if best is None or cost < best[1]:
                best = (v, cost)
    v = best[0]
    bqs, lvl = _unpack(v)
    return bqs, float(np.sqrt(np.mean((biquads_db(bqs) + lvl - target_db) ** 2)))


def convert(data: bytes) -> dict:
    """WAV bytes -> {"words": 119 floats, "error_db": fit error}. Pure numpy, about a second."""
    x, rate = read_wav(data)
    x = prepare(x, rate)
    target = smooth_db(x)
    bqs, err = fit(target)
    mid = (GRID >= 200) & (GRID <= 4000)
    level = LEVEL_DB - float(np.mean(biquads_db(bqs)[mid]))
    p = [0.0] * NUM_WORDS
    for i, bq in enumerate(bqs):
        p[5 * i:5 * i + 5] = bq
    for k in range(4):
        p[35 + 5 * k:40 + 5 * k] = NEUTRAL_GROUP
    p[55] = 10 ** (level / 20)
    p[87:119] = [k * 6 / 32768 for k in range(32)]
    return {"words": p, "error_db": round(err, 2)}


# ---- transfer ------------------------------------------------------------------------------
def slot_base(slot: int) -> list[int]:
    if not 0 <= slot < NUM_SLOTS:
        raise IRError(f"IR slot must be USER 1..{NUM_SLOTS}")
    return [0x60, 0x40 + slot, 0, 0]


def clean_name(name: str) -> str:
    n = "".join(c if 32 <= ord(c) < 127 else "?" for c in str(name)).strip()[:NAME_LEN]
    if not n:
        raise IRError("The IR name is empty.")
    return n


def name_bytes(name: str) -> list[int]:
    return [ord(c) for c in clean_name(name).ljust(NAME_LEN)]


def word_messages(words) -> list[tuple[int, list[int]]]:
    """(offset from the slot base, 7-bit data) per DT1, as BTS sends them."""
    ints = [struct.unpack("<I", struct.pack("<f", float(w)))[0] for w in words]
    ints += [0x20] * max(0, 128 - len(ints))
    out = []
    for start in range(0, len(ints), 30):
        nib = []
        for w in ints[start:start + 30]:
            nib += [(w >> (4 * (7 - j))) & 0xF for j in range(8)]
        out.append((0x10 + start * 8, nib))
    return out


def send(port, slot: int, name: str, words) -> None:
    from .roland import add7
    base = slot_base(slot)
    port.dt1(base, name_bytes(name), pause=0.01)
    for off, data in word_messages(words):
        port.dt1(add7(base, off), data, pause=0.008)
    time.sleep(0.3)
