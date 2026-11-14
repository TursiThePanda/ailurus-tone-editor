# Ailurus Tone Editor — GX-10 IR loader plan (2026-11-14)

Status 2026-11-14: BUILT INTO THE APP (branch feature/save, uncommitted): ailurus_tone/ir.py (numpy-only fitter), GX-10 IR slot read/load/rename, Cabinet IRs dialog, read-only MCP gx_list_irs, tests/test_ir.py. Live-verified into USER-16.

## Goal
Load the player's own cabinet IRs (.wav) into the GX-10's 16 user IR slots (USER-1..16) from
Ailurus, as BOSS TONE STUDIO's IR LOADER does, on macOS, Windows and Linux. KATANA Gen 3 has no
IR loader (only LINE OUT AIR FEEL = CUSTOM), so GX-10 only.

## What is known (BTS for GX-10 1.0.2, js/ir/ir_data_loader.js + product_setting.js)
- Transfer is plain DT1, no special protocol:
  - name: 12 ASCII bytes at 60 40 00 00 + n * 00 01 00 00 (7-bit address, n = 0..15);
  - data: 32-bit words from the converted file (little endian in the file), each sent as 8
    nibbles (high nibble first) at 60 40 00 10 + n * 00 01 00 00, word i at +i*8;
    30 words (240 nibbles) per DT1, 8 ms apart; padded to at least 128 words.
  - BTS waits for no ack after the data (the old slot-restore path then sent SET_IR_DATA).
- Conversion is NOT in the JS: $native.app.control("convert:{path}") runs native code
  (class CIrDataConv; its state holds 20 doubles, five [4 x double] blocks — filter
  coefficients? —, a 16384-double buffer, sample rate fields) and writes a temp file
  (RLD00000.tmp-like name) that the JS reads with $native.fs.readData.
- Reading back (RQ1): IR names are readable (2026-11-14: USER-1 "V30 3 Livepl", USER-2
  "Mesa Boogie ", USER-3..16 empty "USER n"). Only 8 bytes at +0x10 answer, the same in every
  slot: 3F 80 00 00 = float32 1.0 -> the words are most likely float32. The IR data itself
  CANNOT be read back, so an IR slot cannot be backed up from the unit.
- Manual: WAV 44.1/48/96 kHz, 16/24-bit. The unit runs at 48 kHz. Address space per slot
  (00 01 00 00 = 16384 bytes, 8 bytes per word) -> at most ~2046 words.

## Step 1 result (2026-11-14): the GX-10 does NOT convolve the IR
BTS converts every WAV into 119 float32 words (476 bytes, written to $TMPDIR/RLD00000.tmp and
overwritten by each conversion) - a fitted MODEL of the IR, not its samples:
- words 0-34: 7 biquads (b0 b1 b2 -a1 -a2); the first three are always identity, #4 is a
  high-pass near 20 Hz, #5-#7 fitted EQ (#6 and #7 identical in every file);
- words 35-54: 4 groups [g, c, -g, c', gain]; gain is on a 2^(k/6) grid (about 1 dB steps,
  signed), e.g. 0.62996 = 2^-2/3, 1.7818 = 2^5/6;
- words 55-86: 32 tap gains; words 87-118: 32 rising tap delays, quantised to 6/32768
  (0.00018311), up to ~0.03 -> a sparse 32-tap FIR (early reflections).
- Conversion is shift- and level-invariant (a Dirac at sample 0 and a half-level Dirac at
  sample 100 give the same words). A 48 kHz Dirac gives absurd tap gains (~7e6): the fitter is
  ill-conditioned for degenerate input; the 44.1 kHz Dirac gives ~identity.
- The BTS binary is stripped (272 symbols), so its fitting algorithm can only be recovered by
  blind disassembly. Captured files: ~/Kytara/GX-10/IR/ailurus-test/bts-converted/*.bts.bin
  (Mac) for the 6 test inputs.
Consequence: Ailurus cannot just resample and send an IR; it needs a fitter that produces
these 119 parameters. David chose option B (own converter) on 2026-11-14.

## Options after step 1
A. Reverse-engineer BTS's fitter (Ghidra on the stripped arm64/x86_64 binary), port it to
   Python -> same result as BTS. Large and uncertain (numeric optimisation code, no symbols).
B. Write our own fitter for the same model, and check what the unit does with the parameters by
   measuring it: GX-10 is a USB audio interface, so send a test signal through a slot holding
   our parameters and record the output. Large, but ours, testable and possibly better.
C. Smaller first step: an "IR slots" panel that lists/renames the 16 slots and can send a
   parameter file converted by BTS (the 476-byte file); the real fitter later.

## Option B — work plan (David chose it 2026-11-14)
Measuring rig (no cables): the GX-10 is a 4-in/4-out USB audio device in VENDOR driver mode
(seen on the Mac 2026-11-14). Roland's re-amp article: computer -> GX-10 USB out 3/4 ("DRY")
feeds the effect chain, GX-10 -> computer USB in 1/2 ("MAIN") carries the processed sound.
Research tools in a separate venv (numpy, scipy, sounddevice, soundfile) - not app deps.
1. Rig check, no write: exponential sine sweep (Farina) through the CURRENT patch at low level,
   deconvolve -> the patch's impulse response. Proves the chain and the latency/level.
2. Measure the model, writes only to ONE empty IR slot (USER-16) and the TEMP patch (never
   saved): a temp patch with nothing but the cab/IR block on USER-16; send parameter sets that
   isolate one part each (one biquad, one group, one tap), measure, fit the meaning (sign
   convention, delay units, what the 4 groups are, level). Then send BTS's own Mesa fit and
   check model(params) == measurement.
3. Own fitter: from a WAV (resample to 48 kHz, trim the onset, normalise) fit the 7 biquads +
   4 groups + 32 taps to the IR's response (least squares on a log-frequency magnitude + the
   early time response); compare by ear and by measurement with BTS's fit and the original IR.
4. Ship: ir.py (fitter in numpy - check its size in the AppImage/DMG/installer), IR slots
   panel (names, Load .wav…, Rename), name/data DT1 writer. Writes only on the player's action.

## Result of option B (2026-11-14)
- Fitter in the app: 7 RBJ biquads + level, own Levenberg-Marquardt, best of 8 starts, ~1 s; Mesa 48K RMS 1.47 dB (BTS 2.5).
- Level rule: model mean over 200 Hz-4 kHz = -3.2 dB (BTS's own Mesa fit as measured on the unit). Measured after loading through the app: exactly -3.2 dB, model = unit within 0.8 dB except a deep 72 Hz notch.
- Every load is kept as JSON in <backup_dir>/IR (the unit cannot give an IR back); a slot holding an IR is replaced only after the player confirms.
- numpy adds ~7 MB to the macOS app (Accelerate, no OpenBLAS).
- Rejected: scipy (larger bundle; its least_squares found a worse minimum, 4.3 dB, on this problem); a single-start LM (stuck at 3.66 dB).

## Original plan
1. Black-box the conversion (no write): David opens the IR LOADER in BTS, loads each test WAV
   from ~/Kytara/GX-10/IR/ailurus-test (Dirac 48k/24, Dirac 44.1k/16, half Dirac at sample 100,
   impulses at 0/1000/2000/4000, decaying noise) plus the Mesa JP-2C IR, and CANCELS at the
   naming dialog. Claude copies each converted temp file. From them: word format (float32?),
   length, resampling, normalisation, any filtering/windowing.
2. If the conversion is more than resample + trim + scale (the filter blocks suggest EQ or
   minimum phase), reproduce it from the Dirac responses (the conversion of a Dirac is its own
   filter), else read the native code (otool/Ghidra).
3. Write ir.py: wav -> words (pure Python + numpy-free resampler, or numpy if bundled), the DT1
   sender, IR name read/rename. Tests: our conversion of each test WAV == BTS's file.
4. First write: ONE empty slot David names (e.g. USER-16), our conversion of the Mesa IR, then
   A/B by ear against the same IR loaded by BTS into another empty slot. Only on David's word.
5. UI: GX-10 Settings or a panel "IR slots": list 16 names, Load .wav…, Rename. MCP: read-only
   list; loading stays with the player.

## Rejected options and why
- Capture BTS's SysEx with a MIDI monitor instead of the temp file: gives the same words but
  needs a real write to the unit for every test; the temp file needs none.
- Guessing the format and writing it straight to the unit: risks a broken/loud IR on the
  unit, and the result could not be compared with BTS.
- Shipping BTS's native converter: not ours to redistribute, and macOS-only as a library.
