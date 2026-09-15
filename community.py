"""Validate community product files. Used by the PR workflow and the build."""
import json, pathlib, sys
import duckdb, jsonschema
from build import PANEL_IDS
from gtin import valid
from rules import PLAUSIBLE

ROOT = pathlib.Path(__file__).parent
SCHEMA = json.loads((ROOT / "community" / "schema.json").read_text())

def validate_file(path: pathlib.Path) -> list[str]:
    errors = []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return [f"{path.name}: not valid JSON ({e})"]
    for e in jsonschema.Draft202012Validator(SCHEMA).iter_errors(data):
        errors.append(f"{path.name}: {e.json_path}: {e.message}")
    if errors:
        return errors
    if unknown := set(data["nutrients"]) - {str(i) for i in PANEL_IDS}:
        errors.append(f"{path.name}: unknown nutrient ids {sorted(unknown)}, see nutrients.json")
    if path.stem != data["barcode"]:
        errors.append(f"{path.name}: filename must equal barcode {data['barcode']}")
    if not valid(data["barcode"]):
        errors.append(f"{path.name}: barcode fails GTIN checksum")
    n = data["nutrients"]
    row = tuple(n.get(k) for k in ("1008", "1003", "1005", "1004", "1079", "1018"))
    con = duckdb.connect()
    con.execute("CREATE TABLE t(n1008 DOUBLE, n1003 DOUBLE, n1005 DOUBLE, n1004 DOUBLE, n1079 DOUBLE, n1018 DOUBLE)")
    con.execute("INSERT INTO t VALUES (?, ?, ?, ?, ?, ?)", row)
    if con.execute(f"SELECT count(*) FROM t WHERE {PLAUSIBLE}").fetchone()[0] == 0:
        errors.append(f"{path.name}: nutrients fail plausibility: energy 0-950 kcal, macros 0-100 g, energy within 30% of the macros")
    return errors

def main(paths: list[str]) -> int:
    files = [pathlib.Path(p) for p in paths] or sorted((ROOT / "community" / "products").glob("*.json"))
    errors = [e for f in files for e in validate_file(f)]
    print("\n".join(errors) or f"{len(files)} files OK")
    return 1 if errors else 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
