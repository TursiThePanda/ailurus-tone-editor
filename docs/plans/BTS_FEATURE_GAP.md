# What BOSS TONE STUDIO can do that Ailurus Tone Editor cannot (2026-11-14)

Compared against Ailurus 0.3.0 + feature/linux. Sources: Roland's manuals (GX-10:
static.roland.com/manuals/gx-10_bts_pc/en-US/index.html, KATANA Gen 3:
static.roland.com/assets/media/pdf/BTS_KTN3_PC_eng02_W.pdf) and the installed BTS on the Mac
(GX-10 1.0.2, KATANA 1.1.0).

## "Own amps" = the GX-10 IR LOADER (David's request)
- Neither unit lets you add your own AMP models (GX-10: built-in AIRD amps only; no "custom"
  amp type exists in its tables). What BTS for GX-10 has is an **IR LOADER**: up to **16 user
  impulse responses** (.wav cabinet IRs) loaded into the unit and then picked in a patch.
- KATANA Gen 3 has **no IR loader**. Closest is SYSTEM → LINE OUT AIR FEEL = CUSTOM (cabinet /
  mic character of LINE OUT, PHONES/REC and USB).
- How BTS does it (js/ir/ir_data_loader.js): the .wav is converted by BTS's **native code**
  (`$native.app.control("convert:{...}")`, not JavaScript), the result is read as binary and sent
  to the unit as SysEx (IRDATA addresses, `ADDRESS_CONST.COMMAND.IRDATA_INIT`), with a slot name
  of `ProductSetting.irDataNameLength` characters. Slot restore is also in there.
- So building it needs the converted format (sample rate, length, encoding) — it is not in the
  JS. Ways to find it: (1) watch BTS convert a known .wav on the Mac and capture the file or the
  SysEx it sends (needs the GX-10 and David's OK: it WRITES an IR slot); (2) read the native
  binary; (3) search whether someone documented the GX-10/GX-100/GT-1000 IR format.
  WRITING IR SLOTS IS A WRITE TO THE UNIT — only with David's word, never slots he uses.

## GX-10: BTS features Ailurus lacks
| BTS feature | Ailurus |
|---|---|
| WRITE (save edited patch to a user memory), Save as | no (Draft 1 writes nothing) |
| Controller settings (CTL / EXP / ASSIGN 1-20) | read in the patch, no editor |
| LIBRARIAN: livesets, import all memories, export liveset to unit, .tsl to/from file, copy / move / delete / rename memories | backup to JSON only |
| IR LOADER (16 user IRs) | no |
| BOSS TONE EXCHANGE (download / upload livesets, Roland account) | no |
| Tuner | no |
| MENU: hardware settings, play option, MIDI settings, MIDI program map, USB settings, device settings | no |
| ALL DATA BACKUP / restore | patches backup only, no restore |

## KATANA Gen 3: BTS features Ailurus lacks
| BTS feature | Ailurus |
|---|---|
| WRITE tone setting (+ name), CLEAR | no (PATCH_WRITE never sent) |
| Drag the chain on the CHAIN screen | via the CHAIN parameter only |
| LIBRARIAN: livesets, import from KATANA, export to KATANA, file import/export, copy, delete, rename, reorder | backup to JSON only |
| BOSS TONE EXCHANGE | no |
| SYSTEM: GLOBAL EQ, TONE SHAPE, LINE OUT AIR FEEL (REC/LIVE/BLEND/CUSTOM), POWER AMP IN setting, POWER CONTROL, MIDI, GA-FC, USB settings | no |
| ASSIGN (expression pedal / GA-FC functions) | no |
| ALL DATA BACKUP / restore | memories backup only |
| TIPS window | our own hover help |

## What Ailurus has that BTS does not
MCP server (an AI edits live), undo/redo of every change, history of who changed what (player,
unit, AI), Linux, one app for both units.

## Open questions for David
- Order of work: IR loader first (his wish), or WRITE/Save first (prerequisite for keeping any
  sound, including one that uses a user IR)?
- May we write to the GX-10 for the IR investigation, and into which IR slot?
