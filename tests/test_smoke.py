import os, pathlib, sqlite3, pytest
DB = pathlib.Path(__file__).parent.parent / "out" / "foods-US.db"
pytestmark = pytest.mark.skipif(not DB.exists(), reason="no build output")

def test_us_file_is_populated():
    db = sqlite3.connect(DB)
    assert db.execute("SELECT count(*) FROM foods WHERE barcode IS NOT NULL").fetchone()[0] > 200_000
    assert db.execute("SELECT count(*) FROM foods WHERE barcode IS NULL").fetchone()[0] > 5_000
    assert db.execute("SELECT count(*) FROM foods_fts WHERE foods_fts MATCH 'chicken breast'").fetchone()[0] > 10
    assert db.execute("SELECT count(*) FROM portions").fetchone()[0] > 5_000
    assert db.execute("SELECT value FROM meta WHERE key='schemaVersion'").fetchone() == ("1",)
    assert db.execute("SELECT name FROM foods WHERE barcode = '3017620422003'").fetchone()[0].lower().startswith("nutella")
    # the plausibility rule must not drop alcohol
    assert db.execute("SELECT count(*) FROM foods WHERE name LIKE 'Alcoholic beverage, beer%'").fetchone()[0] >= 1

@pytest.mark.skipif(os.environ.get("SKIP_OFF") == "1", reason="built without Open Food Facts")
def test_fr_file_has_open_food_facts():
    db = sqlite3.connect(DB.with_name("foods-FR.db"))
    n = db.execute("SELECT count(*) FROM foods WHERE barcode IS NOT NULL").fetchone()[0]
    assert n > 100_000, "no Open Food Facts rows in foods-FR.db; for a --skip-off build run pytest with SKIP_OFF=1"

def test_starter_fits_in_the_app():
    assert (DB.parent / "foods-starter.zip").stat().st_size < 10 * 1024 * 1024

def test_file_size_budget():
    assert DB.with_suffix(".zip").stat().st_size < 120 * 1024 * 1024
