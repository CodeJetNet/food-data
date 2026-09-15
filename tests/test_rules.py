import duckdb
from rules import PLAUSIBLE

ROWS = [
    # (label, energy, protein, carbs, fat, fiber, alcohol, expected_kept)
    ("nutella", 539, 6.3, 57.5, 30.9, None, None, True),
    ("water", 0, 0, 0, 0, None, None, True),
    ("diet_soda_rounding", 1, 0, 0.2, 0, None, None, True),
    ("missing_energy", None, 10, 10, 10, None, None, False),
    ("energy_too_high", 950, 0, 0, 100, None, None, False),
    ("macros_dont_add_up", 100, 30, 30, 30, None, None, False),     # 510 kcal implied
    ("only_energy_known", 200, None, None, None, None, None, True),
    ("negative_protein", 100, -1, 20, 2, None, None, False),
    ("macros_over_100g", 400, 60, 60, 0, None, None, False),
    ("beer", 43, 0.5, 3.6, 0, 0, 3.9, True),                        # 16 kcal from macros, 27 from alcohol
    ("wheat_bran", 216, 15.5, 64.5, 4.25, 42.8, None, True),         # fiber at 4 kcal/g would imply 358
]

def test_plausible_rule():
    con = duckdb.connect()
    con.execute("CREATE TABLE t(label VARCHAR, n1008 DOUBLE, n1003 DOUBLE, n1005 DOUBLE, n1004 DOUBLE, n1079 DOUBLE, n1018 DOUBLE)")
    con.executemany("INSERT INTO t VALUES (?, ?, ?, ?, ?, ?, ?)", [r[:7] for r in ROWS])
    kept = {r[0] for r in con.execute(f"SELECT label FROM t WHERE {PLAUSIBLE}").fetchall()}
    for label, *_, expected in ROWS:
        assert (label in kept) == expected, label
