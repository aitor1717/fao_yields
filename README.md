# FAO Crop Yields

[![Tests](https://github.com/aitor1717/fao_yields/actions/workflows/test.yml/badge.svg)](https://github.com/aitor1717/fao_yields/actions/workflows/test.yml)

A small data pipeline + dashboard comparing how countries' farmland performs
by dollar value and food energy per hectare, not raw tonnage. Turns FAOSTAT
crop production/area data and World Bank arable-land data into clean CSVs,
visualized in Streamlit.

**[Demo](https://faocropyields.streamlit.app/)**

![Global Crop Yields dashboard, value per arable hectare by country](docs/dashboard-preview.png?v=3)

Dollar-denominated so crops are comparable across countries, median-based so
outlier crops don't dominate, and every gap-filled or estimated figure is
disclosed on the chart itself, not just in a footnote.

## Run it locally

```bash
pip install -r requirements.txt
streamlit run dashboard_app.py
```

Reads directly from the checked-in CSVs in `data/`, no other setup needed.

### Run the tests

```bash
pip install -r requirements-dev.txt
pytest
```

## How the data is built

`dashboard_app.py` reads six CSVs in `data/`, each produced by its own
script from FAOSTAT/World Bank/Eurostat sources (some fetched live via API,
some from large FAOSTAT bulk exports too big to check in; see each script's
docstring for sources and download URLs):

- **`pipeline.py`** turns the raw FAOSTAT crop production/area export into
  `FAO_Crop_Yield_TableauReady.csv` and `FAO_Arable_Land_Productivity.csv`.
  Falls back to correcting the checked-in CSVs when the (uncommitted,
  multi-GB) raw export isn't available, the currently-runnable path.
- **`derive_value_kcal.py`** builds the dollar-value and food-energy figures
  behind most panels, filling an EU coverage gap with
  **`derive_eu_value_gap.py`** (run first) and a handful of FAOSTAT-absent
  countries with **`derive_value_gap_fill.py`**.
- **`fao_filters.py`** holds the filtering logic (livestock exclusion, FAO
  rollup categories, country-name reconciliation) shared by every script
  above, so corrections can't drift out of sync.

## Data caveats

Real pitfalls in the source data, corrected for throughout:

- **Yield is derived, not trusted.** FAOSTAT's own "Yield" element has
  changed units across revisions, so yield is always computed directly as
  `Production_tons / AreaHarvested_ha`.
- **Livestock items and FAO's own rollup/regional aggregates are excluded**
  (e.g. "Cereals; primary", "World"), since left in they'd double-count
  their own constituent crops or countries.
- **`area_data.csv` is not arable land**, despite its header. It's World
  Bank total land area; `arable_land_ha.csv` is the real arable-land figure.
- **Processed derivatives are excluded from arable-land-productivity totals**
  (e.g. "Palm oil" alongside the "Oil palm fruit" it's extracted from), to
  avoid double-counting the same harvest twice.

The dashboard adds two exclusion thresholds (territories under 1,000 km²,
country-years with under 5 reported crops) and uses the median, not the
mean, for cross-crop yield figures: a country's per-crop yields are
right-skewed enough that one or two outliers (e.g. greenhouse produce) can
dominate a mean. Narrower, panel-specific caveats are disclosed inline in
the dashboard's own "Conclusion & Notes" panel.

## Known limitations

- **"Production per Arable Hectare" favors permanent-crop economies** (palm
  oil, bananas, coffee): the World Bank's arable-land denominator excludes
  that land while the production numerator includes it, a scope mismatch
  between the two source datasets, not a bug.
- **FAOSTAT's data-quality flags are discarded.** A country's figures may
  be mostly estimates, and this currently reads the same as official data.
- **Arable land uses one recent snapshot for every year**, not a
  year-matched one. Arable land changes slowly, so the effect is small.

## Project history

This project originally shipped a parallel Tableau workbook alongside the
Streamlit app. It was retired after an audit found its data and formulas
had gone stale relative to the corrected pipeline, quietly contradicting
the Streamlit app's numbers. Streamlit is now the only dashboard.
