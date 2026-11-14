<img src="assets/build/icon-1024.png" width="128" alt="Ailurus Tone Editor icon" align="right">

# Ailurus Tone Editor

Open editor for the BOSS GX-10 and the BOSS KATANA Gen 3 amps — an alternative to BOSS TONE
STUDIO, with its own app window
and an MCP server, so an AI (Claude, Codex, Gemini, LM Studio, ...) can tune your sound live
while you play. **Draft 1** - packaged apps for macOS and Windows; Linux later (runs from source).

Not affiliated with, endorsed by or sponsored by Roland Corporation or BOSS. BOSS, GX-10, KATANA
and BOSS TONE STUDIO are trademarks of Roland Corporation, named here only to say what the app
works with.

Roland's parameter tables are **not** part of this project: they are read at runtime from
your installed BOSS TONE STUDIO for GX-10 / for KATANA Gen 3 (the one for your unit must be
installed, but must not be running). It is found in the usual install place by itself; if
you installed it elsewhere, choose its folder in *Settings → BOSS TONE STUDIO*.

## Run (from source)

    python3.13 -m venv .venv                         # Windows: py -3.12 (python-rtmidi has no 3.13 wheel there)
    .venv/bin/pip install -e ".[dev]"
    .venv/bin/python -m ailurus_tone                 # app window
    .venv/bin/python -m ailurus_tone --server        # no window, UI at http://127.0.0.1:8710/
    .venv/bin/python -m ailurus_tone --demo FILE     # no GX-10: one patch from a backup JSON, nothing sent

Plug the GX-10 straight into the computer (USB MIDI does not work through a hub) and quit
BOSS TONE STUDIO. The app connects by itself. The KATANA Gen 3 needs Roland's *KATANA3
Driver* (the older KATANA driver gives no MIDI port).

With several units plugged in, the unit menu at the top of the window picks the one you
edit; MCP tools take an optional `device` argument, and `gx_select_device` changes the
active one.

## Build the macOS app

    .venv/bin/pip install -e ".[dev]"
    packaging/build_macos.sh

Gives `dist/Ailurus Tone Editor.app` and `dist/Ailurus-Tone-Editor-<version>.dmg` (PyInstaller,
Apple Silicon, macOS 13+). The app is ad-hoc signed, not notarized: after downloading it,
macOS refuses the first start - open System Settings → Privacy & Security → "Open Anyway".
BOSS TONE STUDIO for your unit must still be installed (its tables are read at runtime).

## Build the Windows app

On Windows 10/11 x64 with Python 3.12 and [Inno Setup 6](https://jrsoftware.org/isinfo.php):

    py -3.12 -m venv .venv
    .venv\Scripts\pip install -e ".[dev]"
    powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1

Gives `dist\Ailurus-Tone-Editor-<version>-windows-setup.exe`, a per-user installer (no admin
rights). It is not code-signed, so SmartScreen warns on the first start - *More info → Run
anyway*. BOSS TONE STUDIO keeps its tables inside its .exe on Windows; the app unpacks them
once into `%LOCALAPPDATA%\Ailurus Tone Editor\bts`. A log of the packaged app is in
`%APPDATA%\Ailurus Tone Editor\app.log`.

## Updates

The app checks for a new version once a day and on demand (*Settings → About → Check for
updates*; on macOS also *Ailurus Tone Editor → Check for Updates…*), using
[Sparkle](https://sparkle-project.org) on macOS and [WinSparkle](https://winsparkle.org) on
Windows. Both read `appcast.xml` on the latest GitHub release, which has one item per
platform; every update is verified with an EdDSA signature before it is installed. On macOS
an update installed this way does not trigger "Open Anyway" again.
Releases are built by `.github/workflows/release.yml` when a `v*` tag is pushed.

## MCP

Streamable HTTP at `http://127.0.0.1:8710/mcp` while the app runs. Tools: `gx_status`,
`gx_list_patches`, `gx_read_patch`, `gx_set`, `gx_switch`, `gx_undo`, `gx_redo`,
`gx_select_patch`, `gx_select_device`, `gx_backup_all`, `gx_set_chain` (reorder a straight chain), `gx_history`
(what the player, the unit and the AI changed, including undo; kept in `<config>/history/*.jsonl`).

Claude Code: `claude mcp add --transport http ailurus-tone http://127.0.0.1:8710/mcp`

## What draft 1 does and does not do

- Live edits change the **current sound** (the unit's temp patch) only. No user slot is
  ever written; switching patch discards the edits.
- Undo/Redo (Cmd/Ctrl+Z), one knob drag = one step; a history of every change by you, the AI
  or a knob on the unit, which the AI can read.
- Drag effects to reorder a straight chain.
- Bulk backup of all 198 user patches (U01-1..U66-3) to the default folder or a chosen one.
- KATANA Gen 3: live edits of every block (edit buffer only, memories are never written),
  patch switching (PANEL, A:CH1..B:CH4), signal order shown as on the amp, backup of all
  9 memories. The chain is set with the CHAIN parameter, not by dragging.
- Not yet: Save / Save as / Restore, `.tsl` import/export, Compare, Linux packaging,
  notarized build, MCP bearer token, divider/splitter branch layout.

## Layout

    ailurus_tone/locate.py      find each unit's BOSS TONE STUDIO (Settings, usual places, registry)
    ailurus_tone/rolandbts.py   what every BOSS TONE STUDIO shares: product_setting.js, address_map.js, values
    ailurus_tone/roland.py      generic Roland SysEx RQ1/DT1 transport (device independent)
    ailurus_tone/unit.py        RolandUnit: connection, undo/redo, edited-vs-saved, diary, backup interface
    ailurus_tone/units/         one module per unit + the registry (UNITS in __init__.py)
      gx10.py, gx10_bts.py        GX-10: patch layout, free chain, live edits; tables from effect_parameter.js
      katana3.py, katana3_bts.py  KATANA Gen 3: edit buffer, variations, chain; tables from the BTS window
    ailurus_tone/backup.py      bulk backup + app config
    ailurus_tone/state.py       one owner of all units; active unit, connect/disconnect watcher
    ailurus_tone/mcp_tools.py   MCP tools
    ailurus_tone/api.py         REST for the window
    ailurus_tone/__main__.py    entry point, app window (pywebview)
    ailurus_tone/web/           UI (HTML/CSS/JS)
    ailurus_tone/help/          short hover help texts (en, cs) - our own words
    ailurus_tone/history.py     activity log (what changed, by whom), read by the AI
    tools/make_icon.py          builds the app icon (Icon Composer layers, icns, ico)
    tools/probe_unit.py         read-only first look at a unit not supported yet (BTS tables, identity, RQ1)
    ailurus_tone/about.py       version, links, licence and libraries for Settings → About
    ailurus_tone/updater.py     automatic updates of the packaged app (Sparkle / WinSparkle)
    packaging/                  PyInstaller spec, build_macos.sh (.app, .dmg, Sparkle),
                                build_windows.ps1 + installer.iss (Inno Setup, WinSparkle)
    tools/appcast_add_windows.py  adds the signed Windows item to the release appcast
    .github/workflows/          release build: DMG + Windows installer + signed appcast.xml on a v* tag
    tests/                      unit tests, no unit needed

## Other BOSS units

Every BOSS TONE STUDIO is built on the same Roland framework, and the shared part is in
`rolandbts.py` and `unit.py`. A new unit is one module in `ailurus_tone/units/` plus a line in
the registry. [docs/adding-a-unit.md](docs/adding-a-unit.md) walks through it, starting with
`tools/probe_unit.py`, which only reads. Pull requests are welcome; you need the unit and
its BTS to test.

## Licence

© 2026 David Urban. [PolyForm Noncommercial 1.0.0](LICENSE): free to use, study and change
for personal, hobby, educational and other non-commercial purposes. **Commercial use —
by or for a company, or reselling it — needs my written permission**; ask in
[Issues](https://github.com/TursiThePanda/ailurus-tone-editor/issues).
The red panda artwork is mine and is covered by the same licence.
