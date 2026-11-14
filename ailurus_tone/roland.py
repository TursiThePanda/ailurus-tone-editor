"""Generic Roland SysEx transport (RQ1/DT1) over a MIDI port. Device independent."""
from __future__ import annotations

import queue
import re
import threading
import time

import rtmidi


def checksum(data) -> int:
    return (128 - sum(data) % 128) % 128


def to7(n: int, width: int = 4) -> list[int]:
    out = []
    for _ in range(width):
        out.append(n & 0x7F)
        n >>= 7
    return out[::-1]


def from7(b) -> int:
    n = 0
    for x in b:
        n = (n << 7) | x
    return n


def add7(addr, off: int) -> list[int]:
    return to7(from7(addr) + off)


def hex_addr(addr) -> str:
    return bytes(addr).hex(" ").upper()


# a MIDI port name with what Windows adds around it: optional "2- " in front, " 1" behind
PORT_DECOR = re.compile(r"(?:\d+- )?(.+?)(?: \d+)?")

class NotConnected(RuntimeError):
    pass


class RolandPort:
    """One SysEx conversation partner. All transactions go through one lock.

    Replies to our RQ1 are collected while the lock is held; every other DT1 the unit
    sends by itself (panel changes) goes to `on_unsolicited(addr, data)` on a worker thread.
    """

    def __init__(self, port_name: str, header: list[int], on_unsolicited=None):
        self.port_name = port_name
        self.header = header
        self.on_unsolicited = on_unsolicited
        self.lock = threading.RLock()
        self._inbox: queue.Queue = queue.Queue()
        self._unsolicited: queue.Queue = queue.Queue()
        self._collecting = False
        self.mi = self.mo = None
        threading.Thread(target=self._unsolicited_worker, daemon=True).start()

    # ---- connection ----------------------------------------------------------
    @staticmethod
    def find_port(ports: list[str], name: str) -> int | None:
        """Index of the unit's port. macOS names it exactly ('GX-10'); on Windows rtmidi appends
        the port number ('GX-10 1') and Windows may prefix a second device of the same name
        ('2- GX-10 3')."""
        for i, p in enumerate(ports):
            if PORT_DECOR.fullmatch(p) and PORT_DECOR.fullmatch(p).group(1) == name:
                return i
        return None

    @classmethod
    def available(cls, port_name: str) -> bool:
        try:
            return (cls.find_port(rtmidi.MidiIn().get_ports(), port_name) is not None
                    and cls.find_port(rtmidi.MidiOut().get_ports(), port_name) is not None)
        except Exception:
            return False

    @property
    def connected(self) -> bool:
        return self.mi is not None

    def open(self):
        with self.lock:
            if self.connected:
                return
            mi, mo = rtmidi.MidiIn(), rtmidi.MidiOut()
            ins, outs = mi.get_ports(), mo.get_ports()
            i, o = self.find_port(ins, self.port_name), self.find_port(outs, self.port_name)
            if i is None or o is None:
                raise NotConnected(f"MIDI port '{self.port_name}' not found (ports: {ins})")
            mi.open_port(i)
            mi.ignore_types(sysex=False, timing=True, active_sense=True)
            mo.open_port(o)
            mi.set_callback(self._on_midi)
            self.mi, self.mo = mi, mo
            time.sleep(0.1)

    def close(self):
        with self.lock:
            for p in (self.mi, self.mo):
                try:
                    p.close_port()
                except Exception:
                    pass
            self.mi = self.mo = None

    # ---- incoming ------------------------------------------------------------
    def _on_midi(self, event, _data=None):
        msg, h = event[0], len(self.header)
        if len(msg) < h + 7 or msg[:h] != self.header or msg[h] != 0x12:
            return
        addr, data = msg[h + 1:h + 5], msg[h + 5:-2]
        if self._collecting:
            self._inbox.put((addr, data))
        else:
            self._unsolicited.put((addr, data))

    def _unsolicited_worker(self):
        while True:
            addr, data = self._unsolicited.get()
            if self.on_unsolicited:
                try:
                    self.on_unsolicited(addr, data)
                except Exception as e:  # never kill the worker
                    print("unsolicited handler error:", e)

    # ---- transactions --------------------------------------------------------
    def _send(self, msg):
        if not self.connected:
            raise NotConnected(f"{self.port_name} is not connected")
        self.mo.send_message(msg)

    def rq1(self, addr, size: int, timeout: float = 1.5) -> list[int]:
        a, s = list(addr), to7(size)
        start, end = from7(a), from7(a) + size
        with self.lock:
            while not self._inbox.empty():
                self._unsolicited.put(self._inbox.get_nowait())
            self._collecting = True
            try:
                self._send(self.header + [0x11] + a + s + [checksum(a + s), 0xF7])
                buf: dict[int, int] = {}
                t0 = time.time()
                while time.time() - t0 < timeout:
                    try:
                        raddr, data = self._inbox.get(timeout=0.05)
                    except queue.Empty:
                        continue
                    r = from7(raddr)
                    if not (start <= r < end):
                        self._unsolicited.put((raddr, data))
                        continue
                    # replies use 7-bit addresses; offsets inside one block stay contiguous
                    for i, x in enumerate(data):
                        buf[from7(add7(raddr, i)) - start] = x
                    if len(buf) >= size:
                        return [buf[i] for i in range(size)]
                raise TimeoutError(f"no reply for {hex_addr(a)} (got {len(buf)} of {size} bytes)")
            finally:
                self._collecting = False

    def dt1(self, addr, data, pause: float = 0.02):
        a, d = list(addr), list(data)
        with self.lock:
            self._send(self.header + [0x12] + a + d + [checksum(a + d), 0xF7])
            time.sleep(pause)
