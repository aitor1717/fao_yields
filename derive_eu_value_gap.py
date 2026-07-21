"""
Fills the EU per-crop Value of Production gap that FAOSTAT's QV domain leaves
from 2018 onward (see derive_value_kcal.py and the project docs: all 27 EU member
states report item-level detail through 2017 and none do from 2018 on - a
clean cutoff pointing to an EU-side reporting-format change). Produces
FAO_EU_Crop_Value_Gap.csv (Country/Crop/Year/Value_kUSD), which
derive_value_kcal.py merges in wherever FAOSTAT's own QV has no item-level
row for an EU country/year.

Source: Eurostat's Economic Accounts for Agriculture, dataset aact_eaa01
("value at current prices"), indicator PRD_BP ("Production value at basic
price" - the closest EAA equivalent to FAOSTAT's Gross Production Value),
unit MIO_EUR. Bulk TSV, fetched directly (no manual download step - unlike
the FAOSTAT raw files, Eurostat's SDMX API serves this without needing an
account or a large multi-domain zip):
  https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/aact_eaa01/?format=TSV

This is CURRENT-price EUR, not constant-price like the rest of this
dashboard's dollar figures - deliberately. Eurostat's constant-price series
(aact_eaa04) uses chain-linked volumes on a 2010/2015/2020 rebasing scheme
that doesn't reduce to a single "price level" a specific year's exchange
rate could convert - there's no clean way to turn "2020-based chain-linked
EUR" into "2014-2016 US$" without inventing a bridging assumption this
project avoids elsewhere (see the oil-palm extraction-rate and kcal
food-share decisions). Current-price EUR, converted via that SPECIFIC year's
own USD exchange rate, avoids that problem at the cost of a different, real
one: EU-derived figures reflect that year's actual prices, while the rest of
the map (FAOSTAT QV) reflects constant 2014-2016 prices. For a same-year,
cross-country comparison (which is what every panel on this dashboard
actually does - nothing compares one country's own trend across years using
these EU-derived figures) this is a minor, disclosed approximation, not a
silent error - documented on every panel that uses it.

Exchange rate: FAOSTAT's own Exchange Rates domain (PE), "Local currency
units per USD", annual value - the SAME source FAOSTAT itself uses to
produce its own USD-denominated series, chosen over an independent ECB rate
specifically for methodological consistency with the rest of this project's
dollar figures.
  https://bulks-faostat.fao.org/production/Exchange_rate_E_All_Data_(Normalized).zip

Item coverage is partial, and more narrowly than intended: this table's
codelist defines item codes for vegetables, fruit, grapes, olives, and wine
(AM040000 upward), and an early version of this script mapped 35 items
expecting real data behind all of them. Checked directly - it isn't there.
Only 27 am_item codes have any actual PRD_BP/MIO_EUR row in the live data,
and every one of them falls in AM010000-AM039000: cereals, oilseeds,
protein crops, tobacco, sugar beet, fibre crops, forage. Vegetables, fruit,
potatoes, grapes, olives, and wine (AM040000 and up) return zero rows -
Eurostat evidently tracks item-level value for those elsewhere (likely a
separate crop-statistics domain, not the Economic Accounts for Agriculture
table used here), which this script does not attempt to locate. ITEM_MAP
below is restricted to the 12 field crops that actually have data. This
closes a real, substantial part of the EU gap - cereals and oilseeds are
the largest field-crop categories by value for most EU producers - but
fruit, vegetables, wine, and olives remain unrecovered for the EU after
2017. Documented as a residual, known gap on the affected panels.
"""
from pathlib import Path

import pandas as pd

from fao_filters import WB_TO_FAO_COUNTRY

base_dir = Path(__file__).resolve().parent
raw_dir = base_dir

EU_GEO_TO_COUNTRY = {
    "AT": "Austria", "BE": "Belgium", "BG": "Bulgaria", "HR": "Croatia",
    "CY": "Cyprus", "CZ": "Czechia", "DK": "Denmark", "EE": "Estonia",
    "FI": "Finland", "FR": "France", "DE": "Germany", "EL": "Greece",
    "HU": "Hungary", "IE": "Ireland", "IT": "Italy", "LV": "Latvia",
    "LT": "Lithuania", "LU": "Luxembourg", "MT": "Malta",
    "NL": "Netherlands (Kingdom of the)", "PL": "Poland", "PT": "Portugal",
    "RO": "Romania", "SK": "Slovakia", "SI": "Slovenia", "ES": "Spain",
    "SE": "Sweden",
}

# Eurostat AM item code -> FAOSTAT Crop name. Restricted to the 12 field
# crops confirmed to actually have PRD_BP/MIO_EUR data (see module
# docstring) - vegetables, fruit, potatoes, grapes, olives, and wine are in
# Eurostat's own AM item codelist but return zero rows in this table, so
# there is no matching entry for them here despite unambiguous crop names
# existing (e.g. AM041200 "Tomatoes" is a real code with no real data).
ITEM_MAP = {
    "AM011000": "Wheat",
    "AM012000": "Rye",
    "AM013000": "Barley",
    "AM014000": "Oats",
    "AM015000": "Maize (corn)",
    "AM016000": "Rice",
    "AM019000": "Cereals n.e.c.",
    "AM021100": "Rape or colza seed",
    "AM021200": "Sunflower seed",
    "AM021300": "Soya beans",
    "AM023000": "Unmanufactured tobacco",
    "AM024000": "Sugar beet",
}

print("Fetching Eurostat aact_eaa01 (current-price Value of Production) ...")
eu = pd.read_csv(raw_dir / "eurostat_aact_eaa01.tsv", sep="\t")
key_col = eu.columns[0]
keys = eu[key_col].str.split(",", expand=True)
keys.columns = ["freq", "am_item", "indic_agr", "unit", "geo"]
eu = pd.concat([keys, eu.drop(columns=key_col)], axis=1)
eu.columns = [c.strip() for c in eu.columns]

eu = eu[(eu["indic_agr"] == "PRD_BP") & (eu["unit"] == "MIO_EUR") & (eu["geo"].isin(EU_GEO_TO_COUNTRY))]
eu = eu[eu["am_item"].isin(ITEM_MAP)]
eu["Country"] = eu["geo"].map(EU_GEO_TO_COUNTRY)
eu["Crop"] = eu["am_item"].map(ITEM_MAP)

year_cols = [c for c in eu.columns if c.strip().isdigit()]
eu_long = eu.melt(id_vars=["Country", "Crop"], value_vars=year_cols, var_name="Year", value_name="Value_MEUR_raw")
eu_long["Year"] = eu_long["Year"].str.strip().astype(int)
# Eurostat marks provisional/estimated figures with a trailing letter flag
# (e.g. "417.62 e") and missing values as ":" - strip both, coercing
# unparseable entries to NaN rather than guessing.
eu_long["Value_MEUR"] = pd.to_numeric(
    eu_long["Value_MEUR_raw"].astype(str).str.strip().str.replace(r"[a-zA-Z]", "", regex=True).str.strip().replace(":", None),
    errors="coerce",
)
eu_long = eu_long.dropna(subset=["Value_MEUR"])
# Only the years FAOSTAT QV actually lacks for the EU (see derive_value_kcal.py)
# and that the rest of this dashboard's data can even use - AreaHarvested_ha
# in FAO_Crop_Yield_TableauReady.csv doesn't extend past 2022, so newer
# Eurostat years would just be dropped at the join in derive_value_kcal.py.
eu_long = eu_long[(eu_long["Year"] >= 2018) & (eu_long["Year"] <= 2022)]

print("Loading FAOSTAT's own exchange rates (Local currency units per USD) ...")
# Eurostat's MIO_EUR figures are already converted to EUR for every EU member
# state, Eurozone or not - so this needs one EUR/USD rate per year applied to
# all 27 countries, not each country's own domestic-currency rate. Getting
# this wrong is a real bug this script had: joining on (Country, Year) against
# FAOSTAT's per-country rate silently used Hungary's Forint rate, Poland's
# Zloty rate, etc. against an already-EUR-denominated value - confirmed
# directly, it undervalued Hungary's wheat by ~370x (Forint/USD is ~370,
# EUR/USD is ~0.9). FAOSTAT carries this under two Element codes - "LCU"
# (Local currency units per USD) and "SLC" (Standard local currency units per
# USD) - identical in value for every country checked, but both present, so
# this also filters to one to avoid a doubled join.
fx = pd.read_csv(raw_dir / "Exchange_rate_E_All_Data_(Normalized).csv")
fx_eur = fx[(fx["Element Code"] == "LCU") & (fx["Months"] == "Annual value") & (fx["Currency"] == "Euro")]
fx_eur = fx_eur[fx_eur["Year"].isin(eu_long["Year"].unique())]
fx_eur = fx_eur[["Year", "Value"]].drop_duplicates().rename(columns={"Value": "EUR_per_USD"})
# Only checked for the years actually used below - a handful of older/newer
# years disagree (pre-adoption Eurozone entrants still tagged "Euro" with a
# stale pre-Euro rate, e.g. Lithuania pre-2015), irrelevant here but a real
# reason not to trust this blindly across the dataset's full 1970-2025 span.
assert (fx_eur.groupby("Year")["EUR_per_USD"].nunique() == 1).all(), "Eurozone members disagree on their own currency's rate in a year this script actually uses - investigate before trusting this."

merged = eu_long.merge(fx_eur, on="Year", how="inner")
print(f"EU rows with a matching exchange rate: {len(merged)} / {len(eu_long)}")

# Value_MEUR is in million EUR; EUR_per_USD is "local currency units per USD"
# (i.e. EUR per USD for eurozone members) - so USD = EUR / (EUR per USD).
merged["Value_kUSD"] = merged["Value_MEUR"] * 1_000_000 / merged["EUR_per_USD"] / 1_000

out = merged[["Country", "Crop", "Year", "Value_kUSD"]]
out_path = base_dir / "FAO_EU_Crop_Value_Gap.csv"
out.to_csv(out_path, index=False)
print(f"Saved {len(out)} rows to {out_path}")
print(f"Countries covered: {out['Country'].nunique()} / {len(EU_GEO_TO_COUNTRY)}")
print(f"Crops covered: {out['Crop'].nunique()} / {len(ITEM_MAP)}")
print(f"Years: {sorted(out['Year'].unique())}")

print("\nSpot check - Germany, 2022:")
print(out[(out["Country"] == "Germany") & (out["Year"] == 2022)].sort_values("Value_kUSD", ascending=False).to_string(index=False))
