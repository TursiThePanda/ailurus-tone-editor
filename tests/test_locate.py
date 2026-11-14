"""Finding a unit's BOSS TONE STUDIO in a folder the user chose, with fake install trees."""
import pytest

from ailurus_tone.locate import BTSError, _path_in, locate, search_under
from ailurus_tone.units.gx10_bts import GX10_BTS
from ailurus_tone.units.katana3_bts import KATANA3_BTS


def fake_gx(base):
    js = base / "BOSS TONE STUDIO for GX-10.app" / "Contents" / "Resources" / "html" / "js"
    (js / "config").mkdir(parents=True)
    (js / "config" / "product_setting.js").write_text("var p = { name:\t'GX-10', modelId: '000000000B' };")
    (js / "config" / "effect_parameter.js").write_text("")
    return js


def fake_katana(base):
    html = base / "BTS KATANA" / "html"
    for f in ("js/config/address_map.js", "js/config/resource.js", "export/item.json", "export/layout.div"):
        (html / f).parent.mkdir(parents=True, exist_ok=True)
        (html / f).write_text("")
    (html / "js/config/product_setting.js").write_text("x = { name:\t\t\t'KATANA Gen3', modelId: '010507' }")
    return html


def test_finds_the_tables_inside_the_chosen_folder(tmp_path):
    js = fake_gx(tmp_path / "Somewhere")
    assert locate(GX10_BTS, str(tmp_path / "Somewhere")) == js
    assert locate(GX10_BTS, str(js)) == js                      # the tables folder itself works too


def test_the_wrong_units_bts_is_refused(tmp_path):
    fake_gx(tmp_path)
    with pytest.raises(BTSError, match="KATANA Gen 3 was not found in"):
        locate(KATANA3_BTS, str(tmp_path))
    html = fake_katana(tmp_path / "k")
    assert locate(KATANA3_BTS, str(tmp_path)) == html           # both installed side by side


def test_missing_or_wrong_folder(tmp_path):
    with pytest.raises(BTSError, match="does not exist"):
        locate(GX10_BTS, str(tmp_path / "nope"))
    with pytest.raises(BTSError, match="was not found in"):
        locate(GX10_BTS, str(tmp_path))


def test_depth_limit(tmp_path):
    deep = tmp_path
    for i in range(9):
        deep = deep / f"d{i}"
    fake_gx(deep)
    assert search_under(tmp_path, GX10_BTS) is None


def test_registry_values_to_folders(tmp_path):
    exe = tmp_path / "unins000.exe"
    exe.write_text("")
    assert _path_in(f'"{exe}" /SILENT') == tmp_path
    assert _path_in(f"{exe},0") == tmp_path
    assert _path_in(str(tmp_path)) == tmp_path
    assert _path_in("") is None


def test_windows_bts_exe_with_embedded_html(tmp_path, monkeypatch):
    """Windows BTS keeps the html tree as a zip inside its .exe; it is unpacked into our cache once."""
    import io
    import zipfile
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("html/js/config/product_setting.js", "x = { name:\t'GX-10' }")
        z.writestr("html/js/config/effect_parameter.js", "")
        z.writestr("html/../evil.txt", "never extracted")
    inst = tmp_path / "Program Files (x86)" / "BOSS" / "BOSS TONE STUDIO for GX-10"
    inst.mkdir(parents=True)
    exe = inst / "BOSS TONE STUDIO for GX-10.exe"
    exe.write_bytes(b"MZ" + b"\0" * 5000 + buf.getvalue() + b"\0" * 300)   # zip in the middle
    root = locate(GX10_BTS, str(tmp_path / "Program Files (x86)"))
    assert root.name == "js" and GX10_BTS.is_root(root)
    assert root.is_relative_to(tmp_path / "cache")
    assert not (tmp_path / "cache" / "Ailurus Tone Editor" / "evil.txt").exists()
    assert locate(GX10_BTS, str(inst)) == root                      # second time: from the cache
    with pytest.raises(BTSError):
        locate(KATANA3_BTS, str(inst))                              # a GX-10 exe is not a KATANA BTS


def test_midi_port_names_on_every_platform():
    from ailurus_tone.roland import RolandPort
    find = RolandPort.find_port
    assert find(["IAC Bus 1", "GX-10"], "GX-10") == 1                      # macOS
    assert find(["Microsoft GS Wavetable Synth 0", "GX-10 1"], "GX-10") == 1   # Windows
    assert find(["2- KATANA3 2"], "KATANA3") == 0                         # Windows, 2nd device
    assert find(["GX-100 1", "KATANA3 DAW CTRL 2"], "GX-10") is None      # no prefix matches
    assert find(["KATANA3 DAW CTRL 2"], "KATANA3") is None
    linux = ["Midi Through:Midi Through Port-0 14:0", "GX-10:GX-10 MIDI 1 40:0", "GX-10:GX-10 MIDI 2 40:1"]
    assert find(linux, "GX-10") == 1                                       # Linux: port 1 of 2
    assert find(["KATANA3:KATANA3 MIDI 1 24:0"], "KATANA3") == 0
    assert find(["GX-100:GX-100 MIDI 1 40:0", "GX-10:GX-10 MIDI 2 40:1"], "GX-10") is None


def _gx_exe_bytes():
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("html/js/config/product_setting.js", "x = { name:\t'GX-10' }")
        z.writestr("html/js/config/effect_parameter.js", "")
    return b"MZ" + b"\0" * 100 + buf.getvalue()


def test_linux_unpacks_the_bts_windows_installer(tmp_path, monkeypatch):
    """No BTS for Linux: its Windows installer (.exe or the .zip from boss.info) is unpacked with
    innoextract into our data folder. A fake innoextract writes what the real one does."""
    import stat
    import sys
    import zipfile

    from ailurus_tone import locate as loc
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    payload = tmp_path / "payload.exe"
    payload.write_bytes(_gx_exe_bytes())
    fake = tmp_path / "innoextract"
    fake.write_text(f"""#!{sys.executable}
import sys, shutil, pathlib
a = sys.argv; out = pathlib.Path(a[a.index("--output-dir") + 1]); inst = pathlib.Path(a[-1])
if b"GX10" not in inst.read_bytes():
    sys.exit("Not a supported Inno Setup installer!")
(out / "app").mkdir(parents=True)
shutil.copy({str(payload)!r}, out / "app" / "BOSS TONE STUDIO for GX-10.exe")
""")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(loc, "innoextract", lambda: str(fake))

    inst = tmp_path / "BOSS TONE STUDIO for GX-10 Installer.exe"
    inst.write_bytes(b"MZ GX10 installer")
    root = loc.from_installer(inst, GX10_BTS)
    assert GX10_BTS.is_root(root)
    kept = tmp_path / "data" / "ailurus-tone-editor" / "BOSS TONE STUDIO" / "BOSS TONE STUDIO for GX-10"
    assert (kept / "BOSS TONE STUDIO for GX-10.exe").is_file()
    assert not (kept.parent / ".unpack").exists()
    assert locate(GX10_BTS, str(kept)) == root                       # what Settings stores

    zipped = tmp_path / "gx10_bts_w102.zip"                           # as downloaded from boss.info
    with zipfile.ZipFile(zipped, "w") as z:
        z.write(inst, inst.name)
    assert loc.from_installer(zipped, GX10_BTS) == root

    other = tmp_path / "something else.exe"
    other.write_bytes(b"MZ nope")
    with pytest.raises(BTSError, match="is not the installer of BOSS TONE STUDIO for GX-10"):
        loc.from_installer(other, GX10_BTS)
    with pytest.raises(BTSError, match="not the installer of BOSS TONE STUDIO for KATANA"):
        loc.from_installer(inst, KATANA3_BTS)


def test_newest_windows_bts_on_boss_info():
    from ailurus_tone.locate import newest_download
    listing = '''
      <a href="/support/by_product/gx-10/updates_drivers/509207">BOSS TONE STUDIO for GX-10 Ver.1.0.0 for Windows</a>
      <a href="/support/by_product/gx-10/updates_drivers/510890"><span>BOSS TONE STUDIO for GX-10
        Ver.1.0.10 for macOS</span></a>
      <a href="/support/by_product/gx-10/updates_drivers/510889"><span>BOSS TONE STUDIO for GX-10
        Ver.1.0.2 for Windows</span></a>'''
    pages = {GX10_BTS.releases: listing,
             "https://tw.boss.info/support/by_product/gx-10/updates_drivers/510889":
                 'x <a href="https://static.roland.com/assets/media/zip/gx10_bts_w102.zip">Download</a>'}
    assert newest_download(GX10_BTS, get=pages.__getitem__) == (
        "1.0.2", "https://static.roland.com/assets/media/zip/gx10_bts_w102.zip")
    assert newest_download(GX10_BTS, get=lambda u: "<html>changed</html>") is None

    def offline(u):
        raise OSError("no network")
    assert newest_download(GX10_BTS, get=offline) is None
