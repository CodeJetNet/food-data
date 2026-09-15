"""Build per-country SQLite food databases.

  python build.py                 full build into out/
  python build.py --skip-off      USDA and community only (fast local iteration)
"""
import datetime, hashlib, json, os, pathlib, sqlite3, sys, urllib.request, zipfile
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
OFF_PARQUET = "hf://datasets/openfoodfacts/product-database/food.parquet"
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


def fetch(url: str, cache: pathlib.Path) -> pathlib.Path:
    cache.mkdir(exist_ok=True)
    dest = cache / url.rsplit("/", 1)[1]
    if not dest.exists():
        print("download", url, file=sys.stderr)
        urllib.request.urlretrieve(url, dest)
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


def merge(con) -> None:
    """One row per barcode and one per generic name: source precedence, then newest publication
    (USDA Branded issues a new fdc_id on every relabel), then the plausibility rule. Result table: merged."""
    prec = " ".join(f"WHEN '{k}' THEN {v}" for k, v in PRECEDENCE.items())
    con.execute(f"""
      CREATE OR REPLACE TABLE merged AS
      SELECT {", ".join(STAGED_COLS)} FROM (
        SELECT *, row_number() OVER (
          PARTITION BY coalesce(barcode, 'name:' || lower(name))
          ORDER BY CASE source {prec} ELSE 9 END, published DESC NULLS LAST) AS rn
        FROM staged
      ) WHERE rn = 1 AND {PLAUSIBLE}""")


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
    h = hashlib.md5()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str]) -> None:
    root = ROOT
    cache, out = root / "cache", root / "out"
    out.mkdir(exist_ok=True)
    con = connect(root)
    for key in ("usda_sr", "usda_fndds", "usda_foundation"):
        load_usda_generic(con, key, fetch(SOURCES[key], cache))
    load_usda_branded(con, fetch(SOURCES["usda_branded"], cache))
    # Tasks 1.6 and 1.7 add: load_off, load_community
    merge(con)
    files = []
    for country in [*COUNTRIES, "starter"]:   # "starter" is the small generic-only file the app embeds
        db = out / f"foods-{country}.db"
        write_sqlite(con, country, db, starter=country == "starter")
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
