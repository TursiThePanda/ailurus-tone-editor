"""Add the Windows installer to the appcast that Sparkle's generate_appcast wrote for the DMG.

    python tools/appcast_add_windows.py dist/appcast.xml <download url> '<sign_update output>'

One feed serves both updaters. The macOS item's enclosure gets sparkle:os="macos"; a copy of
the item (same version, build and release notes, without the macOS minimum system version)
gets the Windows enclosure, marked sparkle:os="windows-x64" and signed with the same EdDSA key
(`sign_update --ed-key-file - <setup.exe>` prints 'sparkle:edSignature="..." length="..."').
Sparkle skips enclosures whose os is not macos; WinSparkle skips the macOS one.
Text-level editing on purpose: re-serialising with ElementTree would rename the namespaces.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

INSTALLER_ARGS = "/SILENT /SP- /NOCANCEL /SUPPRESSMSGBOXES"   # Inno Setup: progress only, no questions


def add_windows(feed: str, url: str, signed: str) -> str:
    m = re.search(r"<item>.*?</item>", feed, re.S)
    if not m:
        raise ValueError("no <item> in the appcast")
    mac = m.group(0)
    if 'sparkle:os="windows' in feed:          # (release notes may well say "Windows")
        raise ValueError("the appcast already has a Windows item")
    sig = re.fullmatch(r'\s*sparkle:edSignature="([^"]+)"\s+length="(\d+)"\s*', signed)
    if not sig:
        raise ValueError(f"unexpected sign_update output: {signed!r}")
    enclosure = re.search(r"<enclosure\b[^>]*/>", mac)
    if not enclosure:
        raise ValueError("no <enclosure/> in the macOS item")
    tagged = enclosure.group(0).replace("<enclosure ", '<enclosure sparkle:os="macos" ', 1)
    mac_tagged = mac.replace(enclosure.group(0), tagged)
    # macOS-only requirements (13.0 would block Windows 10/11 in WinSparkle; arm64)
    win = re.sub(r"\s*<sparkle:(minimumSystemVersion|hardwareRequirements)>[^<]*</sparkle:\1>", "", mac)
    win = win.replace(enclosure.group(0),
                      f'<enclosure url="{url}" sparkle:os="windows-x64" '
                      f'sparkle:installerArguments="{INSTALLER_ARGS}" '
                      f'sparkle:edSignature="{sig.group(1)}" length="{sig.group(2)}" '
                      f'type="application/octet-stream"/>')
    return feed.replace(mac, f"{mac_tagged}\n        {win}", 1)


if __name__ == "__main__":
    path, url, signed = sys.argv[1:4]
    p = Path(path)
    p.write_text(add_windows(p.read_text(encoding="utf-8"), url, signed), encoding="utf-8")
