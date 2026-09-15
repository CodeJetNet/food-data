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

## Files

- `nutrients.json`: the canonical nutrient list. USDA nutrient id, Open Food Facts name, unit, daily target.
- `countries.json`: country code to Open Food Facts country tag.
- `sources.json`: USDA download URLs. Update by hand when USDA publishes a new release.
- `schema/`: the SQLite schema, versioned.
