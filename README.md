# food-data

Builds the food database for the calorie tracker app: one SQLite file per country
(`foods-<CC>.zip`) plus a small `foods-starter.zip` the app ships with. The build
runs in GitHub Actions with DuckDB and publishes the files to Cloudflare R2, with a
GitHub Release as the mirror. `manifest.json` at the bucket root lists the current
files.

## Sources

| Source | License |
|---|---|
| [Open Food Facts](https://world.openfoodfacts.org/data) (Parquet export on Hugging Face) | ODbL |
| [USDA FoodData Central](https://fdc.nal.usda.gov/download-datasets) Branded, SR Legacy, FNDDS, Foundation | Public domain |

The output database is published under the [ODbL](https://opendatacommons.org/licenses/odbl/).

## Contributing a food

Add or fix products on Open Food Facts, from the app or at
<https://world.openfoodfacts.org>. Every build picks up the changes.

`community/` is a curator layer for maintainers to override wrong upstream rows: one
JSON file per barcode in `community/products/<gtin13>.json`, validated against
`community/schema.json`. Values are per 100 g or 100 ml, keyed by the nutrient ids in
`nutrients.json`.

## Build

`python build.py` downloads the sources into `cache/`, builds every country file into
`out/` and writes `out/manifest.json`. `--skip-off` leaves Open Food Facts out for fast
local iteration. Needs Python 3.12+ with `requirements.txt` installed (numpy is only
there because DuckDB's Python UDFs need it).

USDA CSV shapes the build corrects for, as found in the releases pinned in `sources.json`:

- FNDDS `food_nutrient.nutrient_id` holds the legacy nutrient *number* (208), not the id
  (1008) that SR Legacy and Foundation use. The build resolves it through each dataset's
  `nutrient.csv`.
- FNDDS `food_portion.csv` leaves `amount` empty, puts the whole text in
  `portion_description` ("1 cup") and a numeric portion code in `modifier`; the code is dropped.
- Foundation `food.csv` also lists lab sub-samples and acquisitions (with nutrients, and
  a few `m/d/Y` publication dates). Only `sr_legacy_food`, `survey_fndds_food` and
  `foundation_food` rows are foods.
- Carbohydrate by difference comes out slightly negative on some Foundation meats
  (-0.43 g). Panel values between -1 and 0 are clamped to 0 before the plausibility rule.

## Files

- `nutrients.json`: the canonical nutrient list. USDA nutrient id, Open Food Facts name, unit, daily target.
- `countries.json`: country code to Open Food Facts country tag.
- `sources.json`: USDA download URLs. Update by hand when USDA publishes a new release.
- `schema/`: the SQLite schema, versioned.
