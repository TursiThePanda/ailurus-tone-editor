"""Finding a unit's BOSS TONE STUDIO in a folder the user chose, with fake install trees."""
import pytest

from ailurus_tone.bts import GX10_BTS
from ailurus_tone.katana_bts import KATANA3_BTS
from ailurus_tone.locate import BTSError, _path_in, locate, search_under


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
