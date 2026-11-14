"""Find a unit's installed BOSS TONE STUDIO (BTS): its tables are read at runtime, never shipped.

Every unit has its own BTS. We look, in this order:
1. the folder the user chose in Settings (any folder that contains the install: the .app on
   macOS, the install folder on Windows - the tables folder inside is found by itself),
2. an environment variable (for development),
3. on Windows, the install location the BTS installer recorded in the registry,
4. the usual install places of the platform.

On macOS the tables are plain files inside the .app. On Windows BTS keeps the same html tree
as a zip embedded in its .exe; we unpack it once into our cache (keyed by the exe's size and
time) and read it from there.

A folder counts as the tables root only if its product_setting.js names the right unit, so a
GX-10 BTS chosen for the KATANA (or the other way round) is refused, not misread.
"""
from __future__ import annotations

import io
import os
import re
import shutil
import struct
import sys
import zipfile
from collections import deque
from dataclasses import dataclass
from pathlib import Path

MAX_DEPTH = 7          # GX-10 on macOS: <app>/Contents/Resources/html/js = 4 levels
MAX_DIRS = 20000       # stop a search started too high up (e.g. the whole Program Files)
PRODUCT_NAME = re.compile(r"\bname:\s*'([^']+)'")


class BTSError(RuntimeError):
    pass


@dataclass(frozen=True)
class BTSProduct:
    unit: str                  # product name inside product_setting.js, e.g. 'GX-10'
    app: str                   # what the user installs, e.g. 'BOSS TONE STUDIO for GX-10'
    env: str                   # environment variable with the tables folder
    setting: str               # relative path of product_setting.js below the tables root
    required: tuple[str, ...]  # further files the reader needs below the root
    mac: tuple[str, ...]       # exact macOS default (fast path)
    html_sub: str = ""         # the tables root relative to BTS's html folder ("js" for the GX-10)

    def is_root(self, d: Path) -> bool:
        f = d / self.setting
        try:
            if not f.is_file() or not all((d / r).is_file() for r in self.required):
                return False
            m = PRODUCT_NAME.search(f.read_text(encoding="utf-8-sig", errors="replace"))
        except OSError:
            return False
        return bool(m and m.group(1) == self.unit)


def search_under(base: Path, product: BTSProduct, max_depth: int = MAX_DEPTH) -> Path | None:
    """Breadth first, so the shallowest match wins; symlinks are not followed."""
    if not base.is_dir():
        return None
    queue, seen = deque([(base, 0)]), 0
    while queue:
        d, depth = queue.popleft()
        if product.is_root(d):
            return d
        found = from_exe(d / f"{product.app}.exe", product)
        if found:
            return found
        seen += 1
        if depth >= max_depth or seen > MAX_DIRS:
            continue
        try:
            subs = sorted(p for p in d.iterdir() if p.is_dir() and not p.is_symlink())
        except OSError:
            continue
        queue.extend((s, depth + 1) for s in subs)
    return None


def cache_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "Ailurus Tone Editor" / "bts"


def _embedded_zip(data: bytes) -> zipfile.ZipFile | None:
    """The zip stored somewhere inside an exe: find its end record and cut it out."""
    end = data.rfind(b"PK\x05\x06")
    if end < 0 or end + 22 > len(data):
        return None
    cd_size, cd_off = struct.unpack("<II", data[end + 12:end + 20])
    comment = struct.unpack("<H", data[end + 20:end + 22])[0]
    start = end - cd_size - cd_off
    if start < 0:
        return None
    try:
        return zipfile.ZipFile(io.BytesIO(data[start:end + 22 + comment]))
    except zipfile.BadZipFile:
        return None


def from_exe(exe: Path, product: BTSProduct) -> Path | None:
    """Windows BTS: unpack the html tree embedded in its .exe into our cache, once per build."""
    try:
        st = exe.stat()
    except OSError:
        return None
    target = cache_dir() / f"{exe.stem}-{st.st_size}-{int(st.st_mtime)}"
    root = target / "html" / product.html_sub if product.html_sub else target / "html"
    if product.is_root(root):
        return root
    try:
        z = _embedded_zip(exe.read_bytes())
    except OSError:
        return None
    if not z:
        return None
    tmp = target.with_name(target.name + ".part")
    shutil.rmtree(tmp, ignore_errors=True)
    with z:
        for info in z.infolist():
            name = info.filename
            if not name.startswith("html/") or ".." in Path(name).parts or Path(name).is_absolute():
                continue
            z.extract(info, tmp)
    if not product.is_root(tmp / root.relative_to(target)):
        shutil.rmtree(tmp, ignore_errors=True)
        return None
    shutil.rmtree(target, ignore_errors=True)
    same_bts = re.compile(re.escape(exe.stem) + r"-\d+-\d+")
    for old in cache_dir().iterdir():                    # older builds of the same BTS
        if same_bts.fullmatch(old.name):
            shutil.rmtree(old, ignore_errors=True)
    tmp.rename(target)
    return root


def platform_places() -> list[Path]:
    """Folders under which BTS is usually installed on this platform."""
    if sys.platform == "darwin":   # never crawl all of /Applications (Xcode alone is huge)
        out = []
        for apps in (Path("/Applications"), Path.home() / "Applications"):
            out += [apps / "BOSS"] + sorted(apps.glob("*TONE STUDIO*")) + sorted(apps.glob("BOSS*"))
        return list(dict.fromkeys(out))
    if sys.platform == "win32":
        out = []
        for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
            if os.environ.get(var):
                out += [Path(os.environ[var]) / "BOSS", Path(os.environ[var]) / "Roland"]
        if os.environ.get("LOCALAPPDATA"):
            out.append(Path(os.environ["LOCALAPPDATA"]) / "Programs")
        return out
    return []


def _path_in(value: str) -> Path | None:
    """'"C:\\x\\unins000.exe" /x' or 'C:\\x\\app.exe,0' or 'C:\\x\\' -> a folder."""
    v = value.strip()
    if v.startswith('"'):
        v = v[1:].split('"', 1)[0]
    else:
        m = re.match(r"(.+?\.exe)\b", v, re.I)
        v = m.group(1) if m else v.split(",")[0]
    if not v:
        return None
    p = Path(v)
    return p if p.is_dir() else (p.parent if p.parent.is_dir() else None)


def registry_places(app: str) -> list[Path]:
    """Install folders the Windows installer of `app` recorded (Uninstall keys)."""
    if sys.platform != "win32":
        return []
    import winreg
    out: list[Path] = []
    keys = [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")]
    for hive, path in keys:
        try:
            root = winreg.OpenKey(hive, path)
        except OSError:
            continue
        with root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                try:
                    with winreg.OpenKey(root, winreg.EnumKey(root, i)) as sub:
                        name = str(winreg.QueryValueEx(sub, "DisplayName")[0])
                        if app.lower() not in name.lower():
                            continue
                        for field in ("InstallLocation", "DisplayIcon", "UninstallString"):
                            try:
                                p = _path_in(str(winreg.QueryValueEx(sub, field)[0]))
                            except OSError:
                                continue
                            if p and p not in out:
                                out.append(p)
                except OSError:
                    continue
    return out


def locate(product: BTSProduct, chosen: str | None = None) -> Path:
    """The tables root of `product`. A folder chosen by the user must hold it (no silent fallback:
    the user picked it on purpose); otherwise every known place is tried."""
    if chosen:
        base = Path(chosen).expanduser()
        if not base.is_dir():
            raise BTSError(f"{product.app}: the chosen folder does not exist: {base}")
        found = search_under(base, product)
        if not found:
            raise BTSError(f"{product.app} was not found in {base}. Choose the folder it is installed in.")
        return found
    env = os.environ.get(product.env)
    if env and product.is_root(Path(env)):
        return Path(env)
    for exact in product.mac if sys.platform == "darwin" else ():
        if product.is_root(Path(exact)):
            return Path(exact)
    for base in registry_places(product.app) + platform_places():
        found = search_under(base, product)
        if found:
            return found
    raise BTSError(f"{product.app} was not found. Install it from boss.info (it does not need to run), "
                   "or choose its folder in Settings.")
