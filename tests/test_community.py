import json, pathlib, pytest
import community, build

GOOD = {"barcode": "3017620422003", "name": "Nutella", "brand": "Ferrero", "serving_size": 15, "serving_unit": "g",
        "nutrients": {"1008": 539, "1003": 6.3, "1005": 57.5, "1004": 30.9}}

def test_validate_good(tmp_path):
    p = tmp_path / "3017620422003.json"; p.write_text(json.dumps(GOOD))
    assert community.validate_file(p) == []

def test_filename_must_match_barcode(tmp_path):
    p = tmp_path / "1234567890128.json"; p.write_text(json.dumps(GOOD))
    assert any("filename" in e for e in community.validate_file(p))

def test_bad_checksum(tmp_path):
    bad = dict(GOOD, barcode="3017620422004")
    p = tmp_path / "3017620422004.json"; p.write_text(json.dumps(bad))
    assert any("checksum" in e for e in community.validate_file(p))

def test_implausible(tmp_path):
    bad = dict(GOOD, nutrients={"1008": 100, "1003": 30, "1005": 30, "1004": 30})
    p = tmp_path / "3017620422003.json"; p.write_text(json.dumps(bad))
    assert any("plausib" in e for e in community.validate_file(p))

def test_community_overrides_everything(tmp_path):
    d = tmp_path / "products"; d.mkdir()
    (d / "3017620422003.json").write_text(json.dumps(GOOD))
    con = build.connect(tmp_path)
    con.execute("INSERT INTO staged (barcode, name, source, source_id, countries, n1008, n1003, n1005, n1004) VALUES ('3017620422003', 'Wrong name', 'off', 'x', ['US'], 539, 6.3, 57.5, 30.9)")
    build.load_community(con, d)
    build.merge(con)
    assert con.execute("SELECT name, source FROM merged").fetchall() == [("Nutella", "community")]

def test_unknown_nutrient_id(tmp_path):
    bad = dict(GOOD, nutrients={**GOOD["nutrients"], "9999": 1})
    p = tmp_path / "3017620422003.json"; p.write_text(json.dumps(bad))
    assert any("unknown nutrient ids ['9999']" in e for e in community.validate_file(p))
