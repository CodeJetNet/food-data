"""Build per-country SQLite food databases.

  python build.py                 full build into out/
  python build.py --skip-off      USDA and community only (fast local iteration)
"""
import datetime, hashlib, json, os, pathlib, shutil, sqlite3, sys, urllib.request, zipfile
import duckdb
from gtin import normalize
from rules import PLAUSIBLE

ROOT = pathlib.Path(__file__).parent
NUTRIENTS = json.loads((ROOT / "nutrients.json").read_text())
PANEL = [n for n in NUTRIENTS if n["panel"]]
PANEL_IDS = [int(n["id"]) for n in PANEL]
ALT_IDS = [int(a[0]) for n in PANEL for a in n.get("alt", [])]   # folded into the panel column, so not long tail
COUNTRIES = json.loads((ROOT / "countries.json").read_text())
SOURCES = json.loads((ROOT / "sources.json").read_text())
SCHEMA_VERSION = 1
# One plain GET. Anonymous hf:// range reads get HTTP 429 from Hugging Face after about a minute (Task 0.2).
OFF_PARQUET = "https://huggingface.co/datasets/openfoodfacts/product-database/resolve/main/food.parquet"
# Barcoded rows: first source wins. Generic rows sharing a name: Foundation, then SR Legacy, then FNDDS.
PRECEDENCE = {"community": 0, "usda_branded": 1, "off": 2, "usda_foundation": 3, "usda_sr": 4, "usda_fndds": 5}
CHUNK = 50_000

STAGED_COLS = ["barcode", "name", "brand", "source", "source_id", "serving_size", "serving_unit",
               "serving_desc", "countries", "published"] + [f"n{i}" for i in PANEL_IDS]


def connect(workdir: pathlib.Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    (workdir / "tmp").mkdir(parents=True, exist_ok=True)
    con.execute(f"SET temp_directory = '{workdir / 'tmp'}'")
    con.create_function("gtin13", lambda c: normalize(c), [str], str, null_handling="special")
    con.execute("CREATE TABLE staged (" + ", ".join(
        f"{c} VARCHAR[]" if c == "countries" else f"{c} DOUBLE" if c.startswith("n") and c[1:].isdigit()
        else f"{c} DOUBLE" if c == "serving_size" else f"{c} VARCHAR" for c in STAGED_COLS) + ")")
    con.execute("CREATE TABLE extra (source VARCHAR, source_id VARCHAR, nutrient INTEGER, per100 DOUBLE)")
    con.execute("CREATE TABLE portion (source VARCHAR, source_id VARCHAR, description VARCHAR, grams DOUBLE)")
    return con


def download(url: str, cache: pathlib.Path) -> pathlib.Path:
    cache.mkdir(exist_ok=True)
    dest = cache / url.rsplit("/", 1)[1]
    if not dest.exists():
        print("download", url, file=sys.stderr)
        part = dest.with_suffix(".part")   # an interrupted download must not pass as complete on the next run
        urllib.request.urlretrieve(url, part)
        part.rename(dest)
    return dest


def fetch(url: str, cache: pathlib.Path) -> pathlib.Path:
    """A USDA zip: download, unzip, return the folder holding the CSVs."""
    dest = download(url, cache)
    folder = dest.with_suffix("")
    if not folder.exists():
        zipfile.ZipFile(dest).extractall(folder)
    return next(folder.rglob("food.csv")).parent


def panel_pivot(id_col: str, amount_col: str) -> str:
    """One column per panel nutrient. Alternate ids from nutrients.json fill in when the primary is absent."""
    cols = []
    for n in PANEL:
        ids = [(n["id"], 1)] + [tuple(a) for a in n.get("alt", [])]
        cols.append("coalesce(" + ", ".join(f"max(CASE WHEN {id_col} = {i} THEN {amount_col} END) * {f}" for i, f in ids) + f") AS n{n['id']}")
    return ",\n".join(cols)


def load_usda_generic(con, source: str, folder: pathlib.Path) -> None:
    """SR Legacy, FNDDS, Foundation: no barcode, has portions."""
    # FNDDS's food_nutrient.nutrient_id holds the legacy nutrient number (208), SR and Foundation the id (1008).
    # nutrient.csv ships with each dataset and maps number to id; numbers stop below 1000 and ids start at 1001.
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE fn AS
      SELECT x.fdc_id, coalesce(m.id, x.nutrient_id) AS nutrient_id, x.amount
      FROM read_csv('{folder}/food_nutrient.csv', header = true) x
      LEFT JOIN read_csv('{folder}/nutrient.csv', header = true) m ON m.nutrient_nbr = x.nutrient_id""")
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE nut AS
      SELECT fdc_id, {panel_pivot('nutrient_id', 'amount')}
      FROM fn
      GROUP BY fdc_id""")
    # Foundation's food.csv also lists lab sub-samples and acquisitions (with nutrients, and a few m/d/Y dates)
    con.execute(f"""
      INSERT INTO staged
      SELECT NULL, f.description, NULL, '{source}', CAST(f.fdc_id AS VARCHAR), 100, 'g', '100 g', NULL,
             CAST(f.publication_date AS VARCHAR), {", ".join(f"n{i}" for i in PANEL_IDS)}
      FROM read_csv('{folder}/food.csv', header = true, types = {{'publication_date': 'VARCHAR'}}) f JOIN nut USING (fdc_id)
      WHERE f.data_type IN ('sr_legacy_food', 'survey_fndds_food', 'foundation_food')""")
    con.execute(f"""
      INSERT INTO extra
      SELECT '{source}', CAST(fdc_id AS VARCHAR), nutrient_id, amount
      FROM fn
      WHERE nutrient_id NOT IN ({", ".join(map(str, PANEL_IDS + ALT_IDS))}) AND amount IS NOT NULL AND amount > 0""")
    # measure_unit.csv holds the unit name ("cup"); 9999 means the description already says it all.
    # FNDDS leaves amount empty, puts "1 cup" in portion_description and a numeric portion code in modifier.
    con.execute(f"""
      INSERT INTO portion
      SELECT '{source}', CAST(p.fdc_id AS VARCHAR),
             trim(concat_ws(' ', rtrim(rtrim(CAST(TRY_CAST(p.amount AS DOUBLE) AS VARCHAR), '0'), '.'),
                            CASE WHEN p.measure_unit_id <> 9999 THEN m.name END,
                            nullif(p.portion_description, ''),
                            CASE WHEN TRY_CAST(p.modifier AS INTEGER) IS NULL THEN nullif(p.modifier, '') END)),
             p.gram_weight
      FROM read_csv('{folder}/food_portion.csv', header = true, types = {{'modifier': 'VARCHAR'}}) p
      LEFT JOIN read_csv('{folder}/measure_unit.csv', header = true) m ON m.id = p.measure_unit_id
      WHERE p.gram_weight > 0""")


def load_usda_branded(con, folder: pathlib.Path) -> None:
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE nut AS
      SELECT fdc_id, {panel_pivot('nutrient_id', 'amount')}
      FROM read_csv('{folder}/food_nutrient.csv', header = true)
      GROUP BY fdc_id""")
    con.execute(f"""
      INSERT INTO staged
      SELECT gtin13(b.gtin_upc), f.description, coalesce(nullif(b.brand_name, ''), b.brand_owner), 'usda_branded',
             CAST(f.fdc_id AS VARCHAR), b.serving_size,
             CASE lower(b.serving_size_unit) WHEN 'grm' THEN 'g' WHEN 'g' THEN 'g' WHEN 'mlt' THEN 'ml' WHEN 'ml' THEN 'ml' END,
             nullif(b.household_serving_fulltext, ''), ['US'], CAST(f.publication_date AS VARCHAR),
             {", ".join(f"n{i}" for i in PANEL_IDS)}
      FROM read_csv('{folder}/food.csv', header = true) f
      JOIN read_csv('{folder}/branded_food.csv', header = true, all_varchar = false) b USING (fdc_id)
      JOIN nut USING (fdc_id)
      WHERE gtin13(b.gtin_upc) IS NOT NULL""")


def load_off(con, parquet: str = OFF_PARQUET) -> None:
    if parquet.startswith("http"):
        parquet = str(download(parquet, ROOT / "cache"))
    con.execute("CREATE OR REPLACE TEMP TABLE country_map(tag VARCHAR, cc VARCHAR)")
    con.executemany("INSERT INTO country_map VALUES (?, ?)", [(tag, cc) for cc, tag in COUNTRIES.items()])
    con.execute(f"""
      CREATE OR REPLACE TEMP VIEW off_raw AS
      SELECT code,
             nullif(coalesce(list_extract(list_filter(product_name, x -> x.lang = 'main'), 1).text, product_name[1].text), '') AS name,
             brands, countries_tags, TRY_CAST(serving_quantity AS DOUBLE) AS serving_quantity, serving_size, nutriments
      FROM read_parquet('{parquet}')
      WHERE len(nutriments) > 0 AND len(countries_tags) > 0""")
    con.execute("""
      CREATE OR REPLACE TEMP TABLE off_countries AS
      SELECT r.code, list(DISTINCT m.cc) AS countries
      FROM off_raw r, unnest(r.countries_tags) AS t(tag) JOIN country_map m ON m.tag = t.tag
      GROUP BY r.code""")
    def col(n):
        v = f"""max(CASE WHEN name = '{n["off"]}' THEN "100g" * {n["off_factor"]} END)"""
        if n["id"] == "1008":   # kilojoules-only products: OFF's `energy` and `energy-kj` are both kJ
            v = f"""coalesce({v}, max(CASE WHEN name IN ('energy-kj', 'energy') THEN "100g" / 4.184 END))"""
        return f"{v} AS n{n['id']}"
    off_pivot = ",\n".join(col(n) for n in PANEL if n["off"])
    missing = [f"NULL AS n{n['id']}" for n in PANEL if not n["off"]]
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE off_nut AS
      SELECT code, {off_pivot}{"," if missing else ""} {", ".join(missing)}
      FROM (SELECT code, unnest(nutriments, recursive := true) FROM off_raw)
      GROUP BY code""")
    con.execute(f"""
      INSERT INTO staged
      SELECT gtin13(r.code), r.name, nullif(r.brands, ''), 'off', r.code, r.serving_quantity,
             CASE WHEN r.serving_quantity IS NOT NULL THEN 'g' END, nullif(r.serving_size, ''), c.countries, NULL,
             {", ".join(f"n.n{i}" for i in PANEL_IDS)}
      FROM off_raw r JOIN off_countries c USING (code) JOIN off_nut n USING (code)
      WHERE gtin13(r.code) IS NOT NULL AND r.name IS NOT NULL""")


def load_community(con, folder: pathlib.Path) -> None:
    for path in sorted(folder.glob("*.json")):
        d = json.loads(path.read_text())
        n = d["nutrients"]
        con.execute(f"INSERT INTO staged VALUES ({', '.join('?' * len(STAGED_COLS))})", [
            d["barcode"], d["name"], d.get("brand"), "community", path.stem, d.get("serving_size"),
            d.get("serving_unit"), d.get("serving_desc"), d.get("countries") or list(COUNTRIES), None,
            *[n.get(str(i)) for i in PANEL_IDS]])


def merge(con) -> None:
    """One row per barcode and one per generic name. The plausibility rule goes first so an implausible newest
    label cannot shadow an older good one; then source precedence, newest publication (USDA Branded issues a new
    fdc_id on every relabel) and source_id so ties are reproducible. The winner carries the countries of every
    source that knows the product. Result table: merged."""
    prec = " ".join(f"WHEN '{k}' THEN {v}" for k, v in PRECEDENCE.items())
    key = "coalesce(barcode, 'name:' || lower(name))"
    # USDA's carbohydrate by difference comes out slightly negative on some meats; a rounding artifact, not a bad row
    con.execute("UPDATE staged SET " + ", ".join(
        f"n{i} = CASE WHEN n{i} BETWEEN -1 AND 0 THEN 0 ELSE n{i} END" for i in PANEL_IDS))
    con.execute(f"""
      CREATE OR REPLACE TABLE merged AS
      SELECT {", ".join(STAGED_COLS)} FROM (
        SELECT * EXCLUDE (countries),
               list_distinct(flatten(list(countries) OVER (PARTITION BY {key}))) AS countries,
               row_number() OVER (
                 PARTITION BY {key}
                 ORDER BY CASE source {prec} ELSE 9 END, published DESC NULLS LAST, source_id DESC) AS rn
        FROM staged
        WHERE {PLAUSIBLE}
      ) WHERE rn = 1""")


def schema_sql() -> str:
    cols = ",\n".join(f"  n{n['id']} REAL" for n in PANEL)
    return (ROOT / "schema" / "v1.sql").read_text().replace("  -- PANEL_COLUMNS", "," + cols[1:])


def write_sqlite(con, country: str | None, out: pathlib.Path, starter: bool = False) -> None:
    """One country file. With starter=True, the generic foods only, no barcodes and no long tail: the file the app ships with."""
    out.unlink(missing_ok=True)
    db = sqlite3.connect(out)
    db.executescript(schema_sql())
    where = "WHERE barcode IS NULL" if starter else f"WHERE barcode IS NULL OR list_contains(countries, '{country}')" if country else ""
    cols = ["barcode", "name", "brand", "source", "source_id", "serving_size", "serving_unit", "serving_desc"] + [f"n{i}" for i in PANEL_IDS]
    cur = con.execute(f"SELECT {', '.join(cols)} FROM merged {where} ORDER BY source, source_id")
    ins = f"INSERT INTO foods ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
    while rows := cur.fetchmany(CHUNK):
        db.executemany(ins, rows)
    if not starter:   # the long tail is most of the generic foods' bytes; the starter file does without it
        db.execute("CREATE TEMP TABLE x(source TEXT, source_id TEXT, nutrient INTEGER, per100 REAL)")
        cur = con.execute("SELECT source, source_id, nutrient, per100 FROM extra")
        while rows := cur.fetchmany(CHUNK):
            db.executemany("INSERT INTO x VALUES (?, ?, ?, ?)", rows)
        db.execute("INSERT OR IGNORE INTO food_nutrients_extra SELECT f.id, x.nutrient, x.per100 FROM x JOIN foods f ON f.source = x.source AND f.source_id = x.source_id")
    db.execute("CREATE TEMP TABLE p(source TEXT, source_id TEXT, description TEXT, grams REAL)")
    cur = con.execute("SELECT source, source_id, description, grams FROM portion")
    while rows := cur.fetchmany(CHUNK):
        db.executemany("INSERT INTO p VALUES (?, ?, ?, ?)", rows)
    db.execute("INSERT INTO portions SELECT f.id, p.description, p.grams FROM p JOIN foods f ON f.source = p.source AND f.source_id = p.source_id")
    db.execute("INSERT INTO foods_fts(foods_fts) VALUES ('rebuild')")
    db.executemany("INSERT INTO meta VALUES (?, ?)", [
        ("schemaVersion", str(SCHEMA_VERSION)),
        ("builtAt", datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")),
        ("country", country or ""),
        ("attribution", "Open Food Facts (ODbL, https://world.openfoodfacts.org), USDA FoodData Central (public domain, https://fdc.nal.usda.gov)"),
    ])
    db.commit()
    db.execute("VACUUM")
    db.close()


def md5(path: pathlib.Path) -> str:
    with open(path, "rb") as f:
        return hashlib.file_digest(f, "md5").hexdigest()


def main(argv: list[str]) -> None:
    root = ROOT
    cache, out = root / "cache", root / "out"
    shutil.rmtree(out, ignore_errors=True)   # a failed run must not leave last run's zips next to a half-written db
    out.mkdir()
    con = connect(root)
    for key in ("usda_sr", "usda_fndds", "usda_foundation"):
        load_usda_generic(con, key, fetch(SOURCES[key], cache))
    load_usda_branded(con, fetch(SOURCES["usda_branded"], cache))
    if "--skip-off" not in argv:
        load_off(con)
    load_community(con, ROOT / "community" / "products")
    merge(con)
    files = []
    for country in [*COUNTRIES, "starter"]:   # "starter" is the small generic-only file the app embeds
        db = out / f"foods-{country}.db"
        write_sqlite(con, None if country == "starter" else country, db, starter=country == "starter")
        zip_path = db.with_suffix(".zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            z.write(db, db.name)
        files.append({"name": zip_path.name, "country": country, "bytes": zip_path.stat().st_size,
                      "md5": md5(db), "url": f"{cdn_url()}/{zip_path.name}", "mirror": f"{mirror_url()}/{zip_path.name}"})
        print(country, db.stat().st_size >> 20, "MB db,", zip_path.stat().st_size >> 20, "MB zip", file=sys.stderr)
    (out / "manifest.json").write_text(json.dumps({
        "schemaVersion": SCHEMA_VERSION,
        "builtAt": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "files": files}, indent=2))


REPO = "https://github.com/codejetnet/food-data"
TAG = os.environ.get("RELEASE_TAG", "local")


def cdn_url() -> str:
    """Cloudflare R2 behind a custom domain: free egress. Pinned to this build's tag, never `latest`."""
    return f"{os.environ.get('CDN_URL', 'https://food.codejet.net')}/builds/{TAG}"


def mirror_url() -> str:
    """The GitHub Release for the same tag. Only a fallback: release bandwidth throttles at real adoption."""
    return f"{REPO}/releases/download/{TAG}"


if __name__ == "__main__":
    main(sys.argv[1:])
