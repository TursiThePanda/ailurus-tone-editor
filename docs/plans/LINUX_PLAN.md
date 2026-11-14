# Ailurus Tone Editor — Linux build plan (agreed with David 2026-11-14)

Status: PLAN, nothing built. Step 0 (hardware probe) PASSED on 2026-11-14 — both units work on Linux without any driver.
Repo: https://github.com/TursiThePanda/ailurus-tone-editor (macOS + Windows released in 0.3.0).
Test machine: Mothership (Fedora 44 KDE, `ssh mothership` from the Mac).

## Goal
A Linux x86_64 release of the app next to the DMG and the Windows installer, built in CI.

## Decisions (David)
1. **Package = AppImage** for a start (one file, any distro). Built by PyInstaller, wrapped with
   appimagetool, in a new `linux` job of .github/workflows/release.yml (ubuntu runner); release
   gets `Ailurus-Tone-Editor-<v>-x86_64.AppImage` + `.sha256`.
2. **UI in the user's default browser** (the existing `--server` mode + open http://127.0.0.1:8710/).
   No bundled GTK/WebKit or Qt WebEngine. A native window can come later if missed.
3. **BOSS TONE STUDIO tables: option (c)** — in Settings → BOSS TONE STUDIO the user picks the
   BTS **Windows installer** downloaded from boss.info (the .exe, or the .zip it comes in); the app
   unpacks it itself with a bundled `innoextract`, then reads the html zip embedded in the BTS .exe
   exactly as on Windows (`locate.from_exe`). Also search a Wine prefix
   (`~/.wine/drive_c/Program Files (x86)/BOSS`) and any folder the user picks (already supported).
4. **Updates: a simple check only.** Once a day + "Check for updates" in Settings → About, ask the
   GitHub API for the latest release; if newer, show "Version X is available" with a link to the
   release page. The user downloads the new AppImage himself. No self-update.

## Step 0 — hardware probe on Mothership: PASSED 2026-11-14 (kernel 7.3.3-200.fc44)
- GX-10: USB 0582:0311, kernel log "Quirk or no altset; falling back to MIDI 1.0", ALSA card with
  two ports `GX-10 MIDI 1` (hw:N,0,0) and `GX-10 MIDI 2`; seq client 'GX-10'. Identity reply
  F0 7E 10 06 02 41 0B 04 00 00 01 00 00 00 F7; RQ1 10 00 00 00 x16 returned the temp patch name.
- KATANA Gen 3 (KATANA-50 EX Gen 3): USB 0582:02f0, same kernel message, ONE port `KATANA3 MIDI 1`;
  works in its normal (vendor) USB mode with NO Roland driver. Identity reply
  F0 7E 10 06 02 41 07 05 00 00 0A 00 00 00 F7; RQ1 20 00 00 00 x16 returned the edit buffer name.
- Probed with `amidi -p hw:N,0,0 -S "<hex>" -d -t 2` (alsa-utils); nothing was written.
- rtmidi (ALSA seq) port names will look like `GX-10:GX-10 MIDI 1 40:0` / `KATANA3:KATANA3 MIDI 1 40:0`.

### Original reasoning for the probe
Unknown whether Linux exposes MIDI for these units at all:
- KATANA Gen 3 needs Roland's KATANA3 Driver on macOS/Windows (vendor-specific USB mode). Roland:
  its "Generic" USB mode supports audio only, NOT MIDI.
- The GX-10 manual also expects a vendor driver. Older Katanas needed kernel quirks
  (bugzilla.kernel.org 195223). snd-usb-audio does auto-detect many Roland vendor interfaces.
- No Linux report found for either unit (searched 2026-11-14).
Probe: plug each unit into Mothership, `lsusb | grep 0582`, `amidi -l`, `aconnect -l`, then one
read-only RQ1 identity request (as tools/probe_unit.py does). Never write. No MIDI ports = stop.

## What to build (if step 0 passes)
- `roland.py` `PORT_DECOR`: ALSA port names look like `GX-10:GX-10 MIDI 1 24:0` — teach
  `find_port` that shape (test next to the macOS/Windows cases in tests/test_locate.py).
- `locate.py`: Linux `platform_places` (Wine prefix, `~/.local/share/Ailurus Tone Editor/bts`),
  `from_installer(path)` = run bundled innoextract into the cache, then `search_under`.
- Settings → BOSS TONE STUDIO on Linux: "Choose installer…" (file picker; in browser mode the
  path is typed or uploaded — decide when building) in addition to the folder.
- `updater.py`: Linux backend = GitHub release check (`releases/latest`, compare versions),
  surfaced in /api/about and as a banner.
- `__main__.py`: on Linux default to server mode + `webbrowser.open`.
- packaging: `packaging/build_linux.sh` (PyInstaller onedir → AppDir with AppRun, .desktop, icon →
  appimagetool), bundle `innoextract` 1.9 static (github.com/dscharrer/innoextract release
  `innoextract-1.9-linux.tar.xz`; zlib-style licence — ship its licence file, add to About libraries).
- README: Linux section (AppImage, `chmod +x`, BTS installer step, udev/permissions if needed).

## Technical details and gotchas
- Both BTS Windows installers are **Inno Setup 5.5.7** (checked 2026-11-14: "Inno Setup Setup Data
  (5.5.7)"; GX-10 1.0.2 is the unicode build) → innoextract 1.9 (supports up to Inno 6.2.2) handles
  them. innoextract has had no release since 2020-08; a future BTS built with Inno ≥ 6.3 may break it.
- Fedora 44 packages innoextract 1.9 too (not needed when bundled).
- python-rtmidi on Linux uses ALSA (needs `libasound.so.2` on the host; present on desktops).
- AppImage runtime type 2 no longer needs libfuse2 on the host (verify with the appimagetool used).
- MCP stays at http://127.0.0.1:8710/mcp.

## Rejected options and why
- **Flatpak** — cleaner window + updates, but needs Flathub review or an own repo, and the
  sandbox makes raw USB/ALSA MIDI access harder. Maybe later.
- **Bundling pywebview's GTK/WebKit2GTK or Qt WebEngine** in the AppImage — huge and fragile across
  distros. Browser UI instead.
- **BTS via Wine only (a)** / **user runs innoextract by hand (b)** — kept as fallbacks (a Wine
  prefix and any folder are still searched), but David wants the app to unpack the installer (c).
- **AppImageUpdate / self-update** — David: a notice is enough, the user downloads the new file.
- **.deb/.rpm** — one package per distro family; AppImage covers all for a start.

## Open questions
- Step 0 result for each unit.
- How the browser UI lets the user pick the installer file (path field vs upload).
