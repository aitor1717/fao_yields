# FAO Crop Yields

A small data pipeline + dashboard that turns FAOSTAT crop production/area
data and World Bank arable-land data into two clean CSVs, visualized in a
Streamlit app. Not a package — flat scripts/CSVs in one directory.

## Setup

```bash
pip install -r requirements.txt
```

## Run the dashboard

```bash
streamlit run dashboard_app.py
```

Reads `FAO_Crop_Yield_TableauReady.csv` and `FAO_Arable_Land_Productivity.csv`
directly — no other setup needed.

## Regenerating the CSVs

Two ways to regenerate the output CSVs, depending on what raw data you have:

- **`apply_fixes_to_existing_output.py`** — the currently-runnable path. Works
  directly on the already-produced `FAO_Crop_Yield_TableauReady.csv` checked
  into this repo, reapplying all the corrections below. Run with:
  ```bash
  python apply_fixes_to_existing_output.py
  ```
- **`pipeline.py`** — the source-of-truth pipeline, run from the full raw
  FAOSTAT bulk export. Needs
  `Production_Crops_Livestock_E_All_Data_(Normalized).csv` in the project
  root (not included here — it's too large to check in; only a small sample,
  `Production_Crops_Livestock_E_All_Data_(Normalized)_sample.csv`, is
  present). Download the full normalized bulk file for the QCL
  (Crops and livestock products) domain from the FAOSTAT site
  (`https://www.fao.org/faostat/en/#data/QCL`), place it in the project root
  under that exact filename, then run:
  ```bash
  python pipeline.py
  ```

Both scripts share their filtering/correction logic via `fao_filters.py`, so
edits to livestock keywords, aggregate items/areas, or the country-name
mapping only need to happen in one place.

## Data caveats

The source data has several real pitfalls that both scripts correct for —
summarized here; full rationale (with the numbers behind each decision) is
in `the project docs`:

- **Yield is derived, not trusted.** FAOSTAT's own "Yield" element has
  changed units across revisions (hg/ha vs kg/ha). Yield is always computed
  as `Production_tons / AreaHarvested_ha` instead.
- **Livestock items are excluded.** FAOSTAT's `Production_Crops_Livestock`
  domain mixes crops with meat/milk/egg/hide/etc. under the same element
  names — these are filtered out via a keyword denylist.
- **FAO's own rollup categories are excluded** (e.g. "Cereals; primary",
  "Fruit Primary") — left in, they double-count their own constituent crops.
- **FAO's own regional/economic rollups are excluded** (e.g. "World",
  "Africa", "European Union (27)") — same double-counting problem at the
  country level.
- **`area_data.csv` is not arable land**, despite its header — it matches
  World Bank "Land area (sq km)". `arable_land_ha.csv` (the real World Bank
  "Arable land (hectares)" indicator) is what's actually used.
- **World Bank and FAO name countries differently** — reconciled via a
  mapping table before joining arable-land data onto production data.

The dashboard itself (`dashboard_app.py`, not the CSVs) applies two further
exclusions and one statistic choice, all documented inline in the app's
"Conclusion & Notes" panel:

- **Territories under 1,000 km²** and **country-years reporting fewer than 5
  distinct crops** are excluded — both guard against a handful of
  concentrated entries (e.g. greenhouse produce in a microstate) dominating
  an otherwise-thin sample.
- **Cross-crop yield figures use the median, not the mean** — a country's
  per-crop yields are right-skewed enough that one or two extreme entries can
  dominate a mean (confirmed directly: Iceland's 2022 mean yield was 144
  t/ha, driven almost entirely by two greenhouse crops, vs. a median of 16
  t/ha across all its reported crops).

## Known limitation

The "Production per Arable Hectare" metric structurally favors economies
whose crop output leans on permanent/tree crops (palm oil, bananas, coffee,
cocoa) — the World Bank's arable-land denominator excludes land under those
crops, while the production numerator includes it. The dashboard panel notes
this; it's a scope mismatch between the two source datasets, not a bug.

## Project history

This project originally shipped a parallel Tableau workbook alongside the
Streamlit app. It's been retired — see `archive/tableau/README.md` for why —
and Streamlit is now the only dashboard.
