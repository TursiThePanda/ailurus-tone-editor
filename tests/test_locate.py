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
