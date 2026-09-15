import sqlite3, pathlib, csv
import build

def write_csv(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)

def write_nutrient_csv(d):
    # every USDA dataset ships nutrient.csv: id (1008) and the legacy nutrient number (208)
    write_csv(d / "nutrient.csv", ["id", "name", "unit_name", "nutrient_nbr", "rank"],
              [[1008, "Energy", "KCAL", 208, 300], [1003, "Protein", "G", 203, 600], [1005, "Carbohydrate, by difference", "G", 205, 1110],
               [1004, "Total lipid (fat)", "G", 204, 800], [1093, "Sodium, Na", "MG", 307, 5800], [1087, "Calcium, Ca", "MG", 301, 5300],
               [1210, "Tryptophan", "G", 501, 18300]])

def make_usda_fixture(root: pathlib.Path):
    d = root / "usda_sr"; d.mkdir(parents=True)
    write_nutrient_csv(d)
    write_csv(d / "food.csv", ["fdc_id", "data_type", "description", "food_category_id", "publication_date"],
              [[1, "sr_legacy_food", "Chicken, broilers or fryers, breast, meat only, raw", 5, "2019-04-01"],
               [2, "sr_legacy_food", "Nonsense with impossible macros", 5, "2019-04-01"],
               # Foundation ships lab sub-samples in food.csv too, a few with m/d/Y dates: not foods
               [3, "sub_sample_food", "Chicken lab sample", 5, "7/19/2023"]])
    write_csv(d / "food_nutrient.csv", ["id", "fdc_id", "nutrient_id", "amount"],
              [[1, 1, 1008, 120], [2, 1, 1003, 22.5], [3, 1, 1005, 0], [4, 1, 1004, 2.6], [5, 1, 1093, 45],
               [6, 1, 1210, 1.2],                       # tryptophan: not in panel, goes to extra table
               [7, 2, 1008, 100], [8, 2, 1003, 30], [9, 2, 1005, 30], [10, 2, 1004, 30],
               [11, 3, 1008, 120], [12, 3, 1003, 22.5], [13, 3, 1005, 0], [14, 3, 1004, 2.6]])
    write_csv(d / "food_portion.csv", ["id", "fdc_id", "seq_num", "amount", "measure_unit_id", "portion_description", "modifier", "gram_weight"],
              [[1, 1, 1, 1, 9999, "", "breast, bone and skin removed", 118],
               [2, 1, 2, 0.5, 1000, "", "chopped", 70]])
    write_csv(d / "measure_unit.csv", ["id", "name"], [[9999, "undetermined"], [1000, "cup"]])
    return d

def test_generic_build(tmp_path):
    fixture = make_usda_fixture(tmp_path)
    con = build.connect(tmp_path)
    build.load_usda_generic(con, "usda_sr", fixture)
    build.merge(con)
    out = tmp_path / "foods-US.db"
    build.write_sqlite(con, "US", out)
    db = sqlite3.connect(out)
    rows = db.execute("SELECT name, source, n1008, n1003, n1093 FROM foods").fetchall()
    assert rows == [("Chicken, broilers or fryers, breast, meat only, raw", "usda_sr", 120.0, 22.5, 45.0)]
    assert db.execute("SELECT nutrient, per100 FROM food_nutrients_extra").fetchall() == [(1210, 1.2)]
    assert db.execute("SELECT description, grams FROM portions ORDER BY grams").fetchall() == [("0.5 cup chopped", 70.0), ("1 breast, bone and skin removed", 118.0)]
    assert db.execute("SELECT count(*) FROM foods_fts WHERE foods_fts MATCH 'chicken'").fetchone()[0] == 1
    build.write_sqlite(con, None, tmp_path / "starter.db", starter=True)
    s = sqlite3.connect(tmp_path / "starter.db")
    assert s.execute("SELECT count(*) FROM foods").fetchone()[0] == 1
    assert s.execute("SELECT count(*) FROM food_nutrients_extra").fetchone()[0] == 0     # no long tail in the starter
    assert db.execute("SELECT value FROM meta WHERE key = 'schemaVersion'").fetchone()[0] == "1"

def make_branded_fixture(root):
    d = root / "usda_branded"; d.mkdir(parents=True)
    # the same GTIN twice: USDA issues a new fdc_id on every relabel, and the newest must win
    write_csv(d / "food.csv", ["fdc_id", "data_type", "description", "food_category_id", "publication_date"],
              [[10, "branded_food", "PEANUT BUTTER, CREAMY", None, "2025-12-01"],
               [11, "branded_food", "PEANUT BUTTER CREAMY (OLD LABEL)", None, "2021-03-01"]])
    write_csv(d / "branded_food.csv", ["fdc_id", "brand_owner", "brand_name", "gtin_upc", "ingredients", "serving_size", "serving_size_unit", "household_serving_fulltext"],
              [[10, "Acme Foods", "ACME", "012345678905", "PEANUTS", 32, "GRM", "2 Tbsp (32 g)"],
               [11, "Acme Foods", "ACME", "012345678905", "PEANUTS", 32, "GRM", "2 Tbsp (32 g)"]])
    write_csv(d / "food_nutrient.csv", ["id", "fdc_id", "nutrient_id", "amount"],
              [[1, 10, 1008, 588], [2, 10, 1003, 25], [3, 10, 1005, 20], [4, 10, 1004, 50],
               [5, 11, 1008, 588], [6, 11, 1003, 25], [7, 11, 1005, 20], [8, 11, 1004, 50],
               [9, 10, 1104, 300]])                      # vitamin A in IU: an alt id, lands in n1106 as 90 µg
    return d

def test_branded_build(tmp_path):
    con = build.connect(tmp_path)
    build.load_usda_branded(con, make_branded_fixture(tmp_path))
    build.merge(con)
    out = tmp_path / "foods-US.db"
    build.write_sqlite(con, "US", out)
    db = sqlite3.connect(out)
    rows = db.execute("SELECT barcode, name, brand, serving_size, serving_unit, serving_desc, n1008, n1106 FROM foods").fetchall()
    assert rows == [("0012345678905", "PEANUT BUTTER, CREAMY", "ACME", 32.0, "g", "2 Tbsp (32 g)", 588.0, 90.0)]
    # not in the CA file
    build.write_sqlite(con, "CA", tmp_path / "foods-CA.db")
    assert sqlite3.connect(tmp_path / "foods-CA.db").execute("SELECT count(*) FROM foods").fetchone()[0] == 0
    # and never in the starter, which has no barcodes
    build.write_sqlite(con, "US", tmp_path / "starter.db", starter=True)
    assert sqlite3.connect(tmp_path / "starter.db").execute("SELECT count(*) FROM foods").fetchone()[0] == 0

def make_fndds_fixture(root):
    # FNDDS 2024-10-31: food_nutrient.nutrient_id holds the nutrient *number* (208), portions carry the text
    # in portion_description with a numeric FNDDS code in modifier and an empty amount
    d = root / "usda_fndds"; d.mkdir(parents=True)
    write_nutrient_csv(d)
    write_csv(d / "food.csv", ["fdc_id", "data_type", "description", "food_category_id", "publication_date"],
              [[3, "survey_fndds_food", "Milk, NFS", 1004, "2022-10-28"]])
    write_csv(d / "food_nutrient.csv", ["id", "fdc_id", "nutrient_id", "amount"],
              [[1, 3, 208, 60], [2, 3, 203, 3.3], [3, 3, 205, 4.9], [4, 3, 204, 3.2], [5, 3, 301, 120], [6, 3, 501, 0.05]])
    write_csv(d / "food_portion.csv", ["id", "fdc_id", "seq_num", "amount", "measure_unit_id", "portion_description", "modifier", "gram_weight"],
              [[1, 3, 1, "", 9999, "1 cup", 10205, 244.0],
               [2, 3, 2, "", 9999, "Quantity not specified", 90000, 0.0]])
    write_csv(d / "measure_unit.csv", ["id", "name"], [[9999, "undetermined"], [1000, "cup"]])
    return d

def test_fndds_shape(tmp_path):
    con = build.connect(tmp_path)
    build.load_usda_generic(con, "usda_fndds", make_fndds_fixture(tmp_path))
    build.merge(con)
    build.write_sqlite(con, "US", tmp_path / "us.db")
    db = sqlite3.connect(tmp_path / "us.db")
    assert db.execute("SELECT name, n1008, n1087 FROM foods").fetchall() == [("Milk, NFS", 60.0, 120.0)]
    assert db.execute("SELECT nutrient, per100 FROM food_nutrients_extra").fetchall() == [(1210, 0.05)]
    assert db.execute("SELECT description, grams FROM portions").fetchall() == [("1 cup", 244.0)]
