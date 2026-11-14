# Adding a BOSS unit

Ailurus Tone Editor supports the GX-10 and the KATANA Gen 3. Other BOSS units that have their
own BOSS TONE STUDIO (BTS) can be added in the same way. Every BTS is built on the same Roland
framework, and everything the units share is already in the app. A new unit is one module with
its own quirks plus one line in the registry.

You need the unit itself and its BTS installed. All tables are read from the installed BTS at
runtime. **Never copy Roland's files into this repository.**

## Safety rules (read these first)

These hold for every unit, and pull requests that break them are not merged:

1. **Live edits go to the current sound only** (the unit's TEMP patch or edit buffer). The app
   never writes a stored patch. A unit usually has a "write" command (on the KATANA it is
   `7F 00 01 04`). Listen for it if it helps, but never send it.
2. **Find out what is hidden.** Some units have internal slots that the panel does not show (on
   the GX-10, slots 199/200). Never read them into a backup and never write them.
3. **Begin read-only.** Until you have checked an address on the unit, send RQ1 (read) only,
   never DT1 (write). `tools/probe_unit.py` cannot write anything.
4. Quit BTS while you work. It polls the unit and garbles your reads.

## 1. Look at the BTS (no unit needed)

    .venv/bin/python tools/probe_unit.py --bts "/Applications/BOSS/<your unit>"

It prints:

- the product name in `product_setting.js`, which `BTSProduct.unit` must match,
- the SysEx header (model id),
- the unit's memory map from `address_map.js`: the current sound, the stored patches, system
  and commands,
- the blocks of each area, with their offsets and sizes.

Both BTS builds seen so far have these files below `js/` (tables in `config/`):

| file | what it holds | read by |
|---|---|---|
| `product_setting.js` | name, version, model id, device id | `rolandbts.read_product` |
| `address_map.js` | every parameter (address, size, ofs, min, max, name) and the block tree | `rolandbts.parse_address_map` |
| `resource.js` | value lists (type names ...) | per unit |
| `businesslogic/**/address_const.js` | command addresses (editor mode, patch select, write) | look them up by hand |

How the effects are described differs between units. The GX-10 has `config/effect_parameter.js`.
The KATANA has none; its labels come from `export/item.json` + `export/layout.div`, and its models
from `js/businesslogic/`. Your unit's module has to deal with that part.

Gotcha: the addresses in `address_map.js` are 4 raw 7-bit **bytes** written as one hex literal
(`0x20100000` = `20 10 00 00`). They are not a number (`rolandbts.addr_bytes`).

## 2. Talk to the unit (read-only)

    .venv/bin/python tools/probe_unit.py --ports
    .venv/bin/python tools/probe_unit.py --bts ... --port "<port>"
    .venv/bin/python tools/probe_unit.py --bts ... --port "<port>" --read <stored patch 1 address> 16

The identity reply should show Roland (41) and the model. Reading the first 16 bytes of a stored
patch usually gives its name. If nothing comes back, check the driver: the KATANA Gen 3 needs the
*KATANA3 Driver*, not the older KATANA driver. Also plug in straight, not through a hub.

## 3. Write the unit module

Create `ailurus_tone/units/<unit>.py` (and `<unit>_bts.py` if reading its tables takes more than
a few lines). Subclass `ailurus_tone.unit.RolandUnit`. Its docstring lists everything the app
calls on a unit. Look at `units/katana3.py` (fixed panel sections) and `units/gx10.py` (a free
chain of 20 blocks) as worked examples.

Declare:

```python
class MyUnit(RolandUnit):
    kind = "myunit"            # id in the API, MCP `device` argument and config.json
    title = "BOSS MY-UNIT"
    aliases = ("my-unit",)
    hint = "How to connect it (driver, no hub ...)."
    bts = BTSProduct(...)      # see locate.py: product name, files it needs, macOS default path
    port_name = "MY-UNIT"
    backup_prefix = "MY-UNIT"

    @classmethod
    def load_tables(cls, bts_folder): ...
```

The base class takes care of the connection (editor mode on/off), undo/redo per patch, "edited vs.
saved", the patch list and slot names, and the backup loop. You write:

- `read_names`, `read_current_number`, `read_current_sound`, `read_saved(n)`, `slot_labels`
- `summary()`, which lists the blocks in signal order (`fx`, `type`, `color`, `on`), and
  `describe_fx(fx)`, which gives each block's parameters (`name`, `value`, `display`, `min`, `max`,
  `options`, `saved`, `changed`)
- `set_param`, `record_change`, `apply_change`, `log_change`
- `_unsolicited(addr, data)`, so that knobs turned on the unit show up in the app and in the diary
- `read_slot`, `slot_name`, `backup_layout` for the backup
- `live_masked`, if values move by themselves (expression pedal positions)

`rolandbts.decode` / `encode` turn the size codes into values.

## 4. Register it

Add the class to `UNITS` in `ailurus_tone/units/__init__.py`. The window, the REST API, the MCP
tools, Settings → BOSS TONE STUDIO and the backup then pick it up without any other change.

## 5. Tests

Add `tests/test_<unit>.py` with a fake port, like `tests/test_katana3.py`. Skip it when the BTS is
not installed. As a minimum, test that:

- edits are sent only to the current-sound address (assert on every address sent),
- stored patches and hidden slots are never written,
- undo/redo restore the value,
- every block decodes.

Then check on the unit itself: read a patch, change a knob in the app, undo it, turn a knob on
the unit, switch patches, and run a backup. Put what you proved in the module's docstring (as
`units/katana3.py` does), so the next person knows what has been checked.
