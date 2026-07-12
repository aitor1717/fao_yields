"""
One-off correction pass applied directly to the already-produced
FAO_Crop_Yield_TableauReady.csv, since the raw FAOSTAT bulk file
(Production_Crops_Livestock_E_All_Data_(Normalized).csv) that pipeline.py
expects is not present in this project folder - only a small sample of it
is. This reproduces what the fixed pipeline.py would have produced, without
needing to re-run the full merge from raw FAO tables.

Once the real raw file is available again, run pipeline.py directly instead
of this script.
"""
import re
import pandas as pd
from pathlib import Path

base_dir = Path("/home/aitor1717/Downloads/Data Analytics/fao_tab_leau")

LIVESTOCK_KEYWORDS = [
    "meat", "milk", "egg", "wool", "hide", "skin", "honey", "beeswax", "cocoon",
    "silk", "cattle", "buffalo", "goat", "sheep", "pig", "swine", "poultry",
    "chicken", "duck", "turkey", "goose", "geese", "horse", "ass", "mule",
    "camel", "rabbit", "offal", "fat", "butter", "cheese", "lard", "tallow",
    "cream", "yoghurt", "casein",
]
livestock_pattern = re.compile("|".join(LIVESTOCK_KEYWORDS), re.IGNORECASE)

WB_TO_FAO_COUNTRY = {
    "Bahamas, The": "Bahamas",
    "Bolivia": "Bolivia (Plurinational State of)",
    "China": "China; mainland",
    "Congo, Dem. Rep.": "Democratic Republic of the Congo",
    "Congo, Rep.": "Congo",
    "Cote d'Ivoire": "Côte d'Ivoire",
    "Egypt, Arab Rep.": "Egypt",
    "Gambia, The": "Gambia",
    "Iran, Islamic Rep.": "Iran (Islamic Republic of)",
    "Korea, Dem. People's Rep.": "Democratic People's Republic of Korea",
    "Korea, Rep.": "Republic of Korea",
    "Kyrgyz Republic": "Kyrgyzstan",
    "Laos": "Lao People's Democratic Republic",
    "Lao PDR": "Lao People's Democratic Republic",
    "Micronesia, Fed. Sts.": "Micronesia (Federated States of)",
    "Moldova": "Republic of Moldova",
    "Netherlands": "Netherlands (Kingdom of the)",
    "Slovak Republic": "Slovakia",
    "St. Kitts and Nevis": "Saint Kitts and Nevis",
    "St. Lucia": "Saint Lucia",
    "St. Vincent and the Grenadines": "Saint Vincent and the Grenadines",
    "Tanzania": "United Republic of Tanzania",
    "Turkiye": "Türkiye",
    "United Kingdom": "United Kingdom of Great Britain and Northern Ireland",
    "United States": "United States of America",
    "Venezuela, RB": "Venezuela (Bolivarian Republic of)",
    "Yemen, Rep.": "Yemen",
    "Somalia, Fed. Rep.": "Somalia",
    "West Bank and Gaza": "Palestine",
}

AGGREGATE_AREAS = {
    "Africa", "Americas", "Asia", "Australia and New Zealand", "Caribbean",
    "Central America", "Central Asia", "China", "Eastern Africa", "Eastern Asia",
    "Eastern Europe", "Europe", "European Union (27)", "Land Locked Developing Countries",
    "Least Developed Countries", "Low Income Food Deficit Countries", "Melanesia",
    "Micronesia", "Middle Africa", "Net Food Importing Developing Countries",
    "Northern Africa", "Northern America", "Northern Europe", "Oceania",
    "Polynesia", "Small Island Developing States", "South America",
    "South-eastern Asia", "Southern Africa", "Southern Asia", "Southern Europe",
    "Western Africa", "Western Asia", "Western Europe", "World",
}

# FAO's Item field has the same problem one level down: rollup categories that
# double-count their own constituent crops (e.g. "Cereals; primary" = Maize +
# Wheat + Rice + ...). "Natural rubber in primary forms" is a real commodity,
# not a rollup, and stays.
AGGREGATE_ITEMS = {
    "Cereals; primary", "Citrus Fruit; Total", "Fibre Crops; Fibre Equivalent",
    "Fruit Primary", "Oilcrops; Cake Equivalent", "Oilcrops; Oil Equivalent",
    "Pulses; Total", "Roots and Tubers; Total", "Sugar Crops Primary",
    "Treenuts; Total", "Vegetables Primary",
}

print("Loading existing FAO_Crop_Yield_TableauReady.csv ...")
df = pd.read_csv(base_dir / "FAO_Crop_Yield_TableauReady.csv")
before_rows, before_crops, before_countries = len(df), df["Crop"].nunique(), df["Country"].nunique()

# 1) Drop livestock/animal-derived items
df = df[~df["Crop"].str.contains(livestock_pattern, regex=True)].copy()
# 1a) Drop FAO's own crop-category rollups
df = df[~df["Crop"].isin(AGGREGATE_ITEMS)].copy()
after_scope_rows, after_scope_crops = len(df), df["Crop"].nunique()

# 1b) Drop FAO's own continental/regional/economic-group rollups (World,
# Africa, EU, "China" parent, etc.) - left in, they silently multi-count any
# cross-country total.
df = df[~df["Country"].isin(AGGREGATE_AREAS)].copy()
after_agg_rows, after_agg_countries = len(df), df["Country"].nunique()

# 2) Recompute yield directly from production / area (fixes the 1000x bug)
df["Yield_tonha"] = df["Production_tons"] / df["AreaHarvested_ha"]
df.loc[df["AreaHarvested_ha"] == 0, "Yield_tonha"] = pd.NA

out_cols = ["Country", "Crop", "Year", "AreaHarvested_ha", "Production_tons", "Yield_tonha"]
df = df[out_cols]

out_path = base_dir / "FAO_Crop_Yield_TableauReady.csv"
df.to_csv(out_path, index=False)

print(f"Rows: {before_rows:,} -> {after_scope_rows:,} (dropped {before_rows - after_scope_rows:,} livestock rows)")
print(f"Distinct items: {before_crops} -> {after_scope_crops} (dropped {before_crops - after_scope_crops} livestock items)")
print(f"Countries/areas: {before_countries} -> {after_agg_countries} (dropped {before_countries - after_agg_countries} aggregate rollups, {after_agg_rows:,} rows remain)")
print("Corrected crop file saved at:", out_path)

# 3) Arable land productivity file
# area_data.csv is NOT arable land (see pipeline.py for the full explanation -
# its values match World Bank "Land area (sq km)" almost exactly, not "Arable
# land (hectares)"). arable_land_ha.csv holds the real World Bank arable-land
# indicator (AG.LND.ARBL.HA), fetched directly from api.worldbank.org.
print("\nBuilding arable land productivity file (using real arable-land data) ...")
df_arable = pd.read_csv(base_dir / "arable_land_ha.csv")[["Country", "ArableLand_ha"]]
df_arable["Country"] = df_arable["Country"].replace(WB_TO_FAO_COUNTRY)

production_by_country_year = df.groupby(["Country", "Year"], as_index=False)["Production_tons"].sum()
df_productivity = production_by_country_year.merge(df_arable, on="Country", how="inner")
df_productivity["ProductionPerArableHa_tons"] = df_productivity["Production_tons"] / df_productivity["ArableLand_ha"]

matched = set(df_productivity["Country"])
unmatched = sorted(set(df_arable["Country"]) - matched)

productivity_path = base_dir / "FAO_Arable_Land_Productivity.csv"
df_productivity.to_csv(productivity_path, index=False)

print(f"Countries in area_data.csv: {df_arable['Country'].nunique()}")
print(f"Countries matched to FAO production data: {len(matched)}")
print(f"Unmatched (region aggregates / micro-territories with no FAO crop data expected): {len(unmatched)}")
print("Arable land productivity file saved at:", productivity_path)

with open(base_dir / "arable_land_unmatched.txt", "w") as f:
    f.write("\n".join(unmatched))
print("Full unmatched list written to arable_land_unmatched.txt for review.")

# Quick sanity spot-check on the unit fix
print("\nSpot check - top 5 countries by 2022 production-per-arable-hectare:")
top5 = df_productivity[df_productivity["Year"] == 2022].sort_values("ProductionPerArableHa_tons", ascending=False).head(5)
print(top5.to_string(index=False))
