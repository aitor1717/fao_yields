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
import pandas as pd
from pathlib import Path

from fao_filters import (
    livestock_pattern,
    AGGREGATE_ITEMS,
    AGGREGATE_AREAS,
    WB_TO_FAO_COUNTRY,
)

base_dir = Path(__file__).resolve().parent

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

# Restricted to rows with a reported harvested area - see pipeline.py for the
# full rationale: processed derivatives (e.g. Palm oil) never have their own
# AreaHarvested and would otherwise double-count the primary crop (Oil palm
# fruit) they were extracted from.
production_by_country_year = (
    df.dropna(subset=["AreaHarvested_ha"])
    .groupby(["Country", "Year"], as_index=False)["Production_tons"].sum()
)
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
