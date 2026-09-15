import sqlite3, pathlib, csv
import duckdb
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
    # the same GTIN three times: USDA issues a new fdc_id on every relabel and the newest plausible label must
    # win, so the old label is listed first and the newest one has macros that fail the plausibility rule
    write_csv(d / "food.csv", ["fdc_id", "data_type", "description", "food_category_id", "publication_date"],
              [[11, "branded_food", "PEANUT BUTTER CREAMY (OLD LABEL)", None, "2021-03-01"],
               [10, "branded_food", "PEANUT BUTTER, CREAMY", None, "2025-12-01"],
               [12, "branded_food", "PEANUT BUTTER, CREAMY (BAD LABEL)", None, "2026-03-01"]])
    write_csv(d / "branded_food.csv", ["fdc_id", "brand_owner", "brand_name", "gtin_upc", "ingredients", "serving_size", "serving_size_unit", "household_serving_fulltext"],
              [[11, "Acme Foods", "ACME", "012345678905", "PEANUTS", 32, "GRM", "2 Tbsp (32 g)"],
               [10, "Acme Foods", "ACME", "012345678905", "PEANUTS", 32, "GRM", "2 Tbsp (32 g)"],
               [12, "Acme Foods", "ACME", "012345678905", "PEANUTS", 32, "GRM", "2 Tbsp (32 g)"]])
    write_csv(d / "food_nutrient.csv", ["id", "fdc_id", "nutrient_id", "amount"],
              [[1, 11, 1008, 588], [2, 11, 1003, 25], [3, 11, 1005, 20], [4, 11, 1004, 50],
               [5, 10, 1008, 588], [6, 10, 1003, 25], [7, 10, 1005, 20], [8, 10, 1004, 50],
               [9, 10, 1104, 300], [10, 10, 1106, 80],   # vitamin A: the primary id (80 µg) wins over the 300 IU alt
               [11, 12, 1008, 100], [12, 12, 1003, 30], [13, 12, 1005, 30], [14, 12, 1004, 30]])   # 510 kcal implied
    return d

def test_branded_build(tmp_path):
    con = build.connect(tmp_path)
    build.load_usda_branded(con, make_branded_fixture(tmp_path))
    build.merge(con)
    out = tmp_path / "foods-US.db"
    build.write_sqlite(con, "US", out)
    db = sqlite3.connect(out)
    rows = db.execute("SELECT barcode, name, brand, serving_size, serving_unit, serving_desc, n1008, n1106 FROM foods").fetchall()
    assert rows == [("0012345678905", "PEANUT BUTTER, CREAMY", "ACME", 32.0, "g", "2 Tbsp (32 g)", 588.0, 80.0)]
    # not in the CA file
    build.write_sqlite(con, "CA", tmp_path / "foods-CA.db")
    assert sqlite3.connect(tmp_path / "foods-CA.db").execute("SELECT count(*) FROM foods").fetchone()[0] == 0
    # and never in the starter, which has no barcodes
    build.write_sqlite(con, "US", tmp_path / "starter.db", starter=True)
    assert sqlite3.connect(tmp_path / "starter.db").execute("SELECT count(*) FROM foods").fetchone()[0] == 0

def make_off_fixture(root):
    p = root / "off.parquet"
    # serving_quantity is VARCHAR in the real Parquet (Task 0.2), so the fixture carries it as a string
    duckdb.connect().execute(f"""
      COPY (SELECT * FROM (VALUES
        ('3017620422003', [{{'lang': 'main', 'text': 'Nutella'}}], 'Ferrero', ['en:france', 'en:united-states'], '15.0', '15 g',
         [{{'name': 'energy-kcal', '100g': 539.0}}, {{'name': 'proteins', '100g': 6.3}}, {{'name': 'carbohydrates', '100g': 57.5}},
          {{'name': 'fat', '100g': 30.9}}, {{'name': 'sodium', '100g': 0.043}}]),
        ('96385074', [{{'lang': 'main', 'text': 'Mystery snack'}}], NULL, ['en:germany'], NULL, NULL,
         [{{'name': 'energy-kj', '100g': 1674.0}}]),     -- kilojoules only, as many European products are
        ('4006381333931', [{{'lang': 'main', 'text': ''}}], 'Nameless', ['en:united-states'], NULL, NULL,
         [{{'name': 'energy-kcal', '100g': 100.0}}])     -- an empty name is no name
      ) t(code, product_name, brands, countries_tags, serving_quantity, serving_size, nutriments)) TO '{p}' (FORMAT PARQUET)""")
    return p

def test_off_build(tmp_path):
    con = build.connect(tmp_path)
    build.load_off(con, str(make_off_fixture(tmp_path)))
    build.merge(con)
    build.write_sqlite(con, "US", tmp_path / "us.db")
    build.write_sqlite(con, "DE", tmp_path / "de.db")
    us = sqlite3.connect(tmp_path / "us.db").execute("SELECT barcode, name, brand, serving_size, n1008, n1093 FROM foods").fetchall()
    assert us == [("3017620422003", "Nutella", "Ferrero", 15.0, 539.0, 43.0)]      # sodium g -> mg
    de = sqlite3.connect(tmp_path / "de.db").execute("SELECT barcode, name, round(n1008) FROM foods").fetchall()
    assert de == [("0000096385074", "Mystery snack", 400.0)]      # 1674 kJ / 4.184

def test_precedence_prefers_usda_branded_over_off(tmp_path):
    con = build.connect(tmp_path)
    build.load_usda_branded(con, make_branded_fixture(tmp_path))
    con.execute("INSERT INTO staged (barcode, name, source, source_id, countries, n1008, n1003, n1005, n1004) VALUES ('0012345678905', 'OFF duplicate', 'off', 'x', ['US'], 500, 20, 20, 40)")
    build.merge(con)
    assert con.execute("SELECT name FROM merged").fetchall() == [("PEANUT BUTTER, CREAMY",)]

def test_winner_carries_every_source_country(tmp_path):
    # USDA Branded knows the product in the US, Open Food Facts in France: it belongs in both files
    con = build.connect(tmp_path)
    build.load_usda_branded(con, make_branded_fixture(tmp_path))
    con.execute("INSERT INTO staged (barcode, name, source, source_id, countries, n1008, n1003, n1005, n1004) VALUES ('0012345678905', 'Beurre de cacahuète', 'off', 'x', ['FR'], 588, 25, 20, 50)")
    build.merge(con)
    for cc in ("US", "FR"):
        build.write_sqlite(con, cc, tmp_path / f"{cc}.db")
        assert sqlite3.connect(tmp_path / f"{cc}.db").execute("SELECT name FROM foods").fetchall() == [("PEANUT BUTTER, CREAMY",)], cc

def test_small_negative_macro_clamped_to_zero(tmp_path):
    # USDA's carbohydrate by difference comes out slightly negative on some meats: clamp, do not drop
    con = build.connect(tmp_path)
    con.execute("INSERT INTO staged (name, source, source_id, n1008, n1003, n1005, n1004) VALUES ('Chicken, breast, meat and skin, raw', 'usda_foundation', '1', 132.8, 21.4, -0.43, 4.78)")
    build.merge(con)
    assert con.execute("SELECT name, n1005 FROM merged").fetchall() == [("Chicken, breast, meat and skin, raw", 0.0)]

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
