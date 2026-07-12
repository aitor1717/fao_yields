import pandas as pd
from pathlib import Path
import unicodedata

from fao_filters import (
    livestock_pattern,
    AGGREGATE_ITEMS,
    AGGREGATE_AREAS,
    WB_TO_FAO_COUNTRY,
)

# =====================================================================================
# FAO Data Pipeline
# =====================================================================================
# This pipeline generates two Tableau-ready CSVs from the tables found in the FAO
# official site: https://www.fao.org/faostat/en/#data/QCL
#
#   1. FAO_Crop_Yield_TableauReady.csv        - per Country/Crop/Year production,
#                                                harvested area, and yield.
#   2. FAO_Arable_Land_Productivity.csv        - per Country/Year total crop
#                                                production per hectare of arable
#                                                land (area_data.csv), i.e. what a
#                                                country produces relative to the
#                                                cultivable land it actually has.
# =====================================================================================

# Define base path
base_dir = Path(__file__).resolve().parent

# File paths
all_data_path = base_dir / 'Production_Crops_Livestock_E_All_Data_(Normalized).csv'
area_codes_path = base_dir / 'Production_Crops_Livestock_E_AreaCodes.csv'
item_codes_path = base_dir / 'Production_Crops_Livestock_E_ItemCodes.csv'
element_codes_path = base_dir / 'Production_Crops_Livestock_E_Elements.csv'
flags_path = base_dir / 'Production_Crops_Livestock_E_Flags.csv'
# NOTE: area_data.csv (the file originally used here) is NOT arable land despite
# its header. Its values match the World Bank "Land area (sq. km)" indicator
# (AG.LND.TOTL.K2) almost exactly for every country checked (e.g. Egypt
# 1,001,450; Australia 7,741,220; Saudi Arabia 2,149,690 - all textbook total
# land area figures in km2, not arable land in hectares). Using it as "arable
# land" would both use the wrong metric (total territory, not farmable land)
# and the wrong unit (km2 read as ha, a further 100x error). arable_land_ha.csv
# is the real World Bank "Arable land (hectares)" indicator (AG.LND.ARBL.HA,
# most recent value per country, fetched from api.worldbank.org) and is what
# should be used as the comparison base instead.
arable_land_path = base_dir / 'arable_land_ha.csv'

# Load data
df_base = pd.read_csv(all_data_path, dtype=str)
df_area = pd.read_csv(area_codes_path, dtype=str)
df_item = pd.read_csv(item_codes_path, dtype=str)
df_element = pd.read_csv(element_codes_path, dtype=str)
df_flag = pd.read_csv(flags_path, dtype=str)

# Normalize column names
def normalize_columns(df):
    df.columns = [unicodedata.normalize("NFKD", col.strip())
                  .encode("ascii", "ignore")
                  .decode("ascii")
                  .replace(" ", "_")
                  for col in df.columns]
    return df

df_base = normalize_columns(df_base)
df_area = normalize_columns(df_area)
df_item = normalize_columns(df_item)
df_element = normalize_columns(df_element)
df_flag = normalize_columns(df_flag)

# Normalize key values
def normalize_keys(df, keys):
    for key in keys:
        df[key] = df[key].astype(str).str.replace(r"[^\x00-\x7F]+", "", regex=True)
        df[key] = df[key].str.strip().str.lstrip("0")
    return df

df_base = normalize_keys(df_base, ["Area_Code", "Item_Code", "Element_Code"])
df_area = normalize_keys(df_area, ["Area_Code"])
df_item = normalize_keys(df_item, ["Item_Code"])
df_element = normalize_keys(df_element, ["Element_Code"])

# Validate columns
def validate_columns(df, expected, name):
    missing = set(expected) - set(df.columns)
    if missing:
        raise ValueError(f"{name} missing columns: {missing}")

validate_columns(df_base, ["Area_Code", "Item_Code", "Element_Code", "Year", "Value"], "Base")
validate_columns(df_area, ["Area_Code", "Area"], "Area")
validate_columns(df_item, ["Item_Code", "Item"], "Item")
validate_columns(df_element, ["Element_Code", "Element"], "Element")
validate_columns(df_flag, ["Flag", "Description"], "Flag")

# Merge with explicit renaming
df = df_base.copy()
df = df.merge(df_area.rename(columns={"Area": "Area_Name"})[["Area_Code", "Area_Name"]], on="Area_Code", how="left", validate="many_to_one")
df = df.merge(df_item.rename(columns={"Item": "Item_Name"})[["Item_Code", "Item_Name"]], on="Item_Code", how="left", validate="many_to_one")
df = df.merge(df_element.rename(columns={"Element": "Element_Name"})[["Element_Code", "Element_Name"]], on="Element_Code", how="left", validate="many_to_one")

# Null check
def assert_no_nulls(df, cols, name):
    nulls = df[cols].isnull().sum()
    if nulls.any():
        raise ValueError(f"{name} contains nulls in joined columns:\n{nulls}")

assert_no_nulls(df, ["Area_Name", "Item_Name", "Element_Name"], "Post-Merge")

# Scope: this pipeline reports on CROPS only. FAOSTAT's Production_Crops_Livestock
# domain also carries livestock and animal-derived products (meat, milk, eggs,
# hides, rendered fat, etc.) under the same Element names ("Production", "Yield"),
# so they must be excluded explicitly rather than assumed away by the Element filter.
# See fao_filters.py for the keyword denylist and rollup-category/area sets below.
df = df[~df["Item_Name"].str.contains(livestock_pattern, regex=True)]
df = df[~df["Item_Name"].isin(AGGREGATE_ITEMS)]
df = df[~df["Area_Name"].isin(AGGREGATE_AREAS)]

# Filter to the elements needed to derive yield ourselves (see below) rather than
# trusting FAOSTAT's own "Yield" element, whose unit varies by revision/domain.
df = df[df["Element_Name"].isin(["Production", "Area harvested"])]
df["Year"] = pd.to_numeric(df["Year"], errors="coerce")
df = df[df["Year"].between(2005, 2022)]
df["Value"] = pd.to_numeric(df["Value"], errors="coerce")

# Validate shape
if df["Year"].min() < 2005 or df["Year"].max() > 2022:
    raise ValueError("Year range out of bounds")
if df["Value"].isnull().all():
    raise ValueError("All values are NaN")

# ---------------------------------------------------------------------------
# Pivot - guard against Element_Code collisions. FAOSTAT reuses the labels
# "Production" and "Yield" across several Element_Codes (crop vs. livestock
# reporting conventions). If the same Area/Item/Year ever carries two rows for
# the same Element_Name, that's a real ambiguity that must not be resolved
# silently (the previous version used aggfunc="first", which drops one value
# with no warning).
# ---------------------------------------------------------------------------
pivot_input = df[["Area_Name", "Item_Name", "Year", "Element_Name", "Value"]].dropna()

dupes = pivot_input.duplicated(subset=["Area_Name", "Item_Name", "Year", "Element_Name"], keep=False)
if dupes.any():
    sample = pivot_input[dupes].sort_values(["Area_Name", "Item_Name", "Year", "Element_Name"]).head(10)
    raise ValueError(
        f"{dupes.sum()} duplicate (Area, Item, Year, Element) rows found - "
        f"resolve the Element_Code collision before pivoting. Sample:\n{sample}"
    )

df_pivot = pivot_input.pivot_table(
    index=["Area_Name", "Item_Name", "Year"],
    columns="Element_Name",
    values="Value",
    aggfunc="first"
).reset_index()

df_pivot.columns.name = None
df_pivot.columns = [str(col).strip() for col in df_pivot.columns]

# Rename
df_pivot = df_pivot.rename(columns={
    "Area_Name": "Country",
    "Item_Name": "Crop",
    "Production": "Production_tons",
    "Area harvested": "AreaHarvested_ha",
})

# Yield is derived directly from production and harvested area (tons / hectare),
# rather than taken from FAOSTAT's own "Yield" element - that field's unit has
# changed across FAOSTAT revisions (hg/ha vs. kg/ha) and silently renaming it to
# "_tonha" previously produced values inflated by exactly 1000x.
df_pivot["Yield_tonha"] = df_pivot["Production_tons"] / df_pivot["AreaHarvested_ha"]
# A handful of rows report nonzero production against zero harvested area
# (protected/greenhouse cultivation, rounding in the source data) - that's a
# genuine divide-by-zero, not a number, so leave it blank rather than inf.
df_pivot.loc[df_pivot["AreaHarvested_ha"] == 0, "Yield_tonha"] = pd.NA

# Export
output_path = base_dir / 'FAO_Crop_Yield_TableauReady.csv'
df_pivot.to_csv(output_path, index=False)

# Validate output
df_check = pd.read_csv(output_path)
expected_cols = ["Country", "Crop", "Year", "Yield_tonha", "Production_tons", "AreaHarvested_ha"]
missing = set(expected_cols) - set(df_check.columns)
if missing:
    raise ValueError(f"Final file missing columns: {missing}")

print("Pipeline completed. File saved at:", output_path)

# =====================================================================================
# Arable land productivity
# =====================================================================================
# Answers: what does each country produce relative to the arable land it has to
# work with? area_data.csv is World Bank arable-land-by-country data, so country
# names need reconciling against FAO's naming before joining.
# =====================================================================================
# WB_TO_FAO_COUNTRY (World Bank -> FAO country-name reconciliation) lives in
# fao_filters.py, shared with apply_fixes_to_existing_output.py.

df_arable = pd.read_csv(arable_land_path)
validate_columns(df_arable, ["Country", "ArableLand_ha"], "ArableLand")
df_arable = df_arable[["Country", "ArableLand_ha"]].copy()
df_arable["Country"] = df_arable["Country"].replace(WB_TO_FAO_COUNTRY)

# Total crop production per Country/Year, from the crop-only pivot above.
production_by_country_year = (
    df_pivot.groupby(["Country", "Year"], as_index=False)["Production_tons"].sum()
)

# Arable land is a single recent snapshot per country (it changes slowly), so it's
# joined onto every year of production rather than requiring a matching year.
df_productivity = production_by_country_year.merge(df_arable, on="Country", how="inner")
df_productivity["ProductionPerArableHa_tons"] = (
    df_productivity["Production_tons"] / df_productivity["ArableLand_ha"]
)

matched_countries = set(df_productivity["Country"])
unmatched = sorted(set(df_arable["Country"]) - matched_countries)
if unmatched:
    print(f"NOTE: {len(unmatched)} area_data.csv entries have no matching FAO crop "
          f"data (World Bank region/income-group aggregates and micro-territories "
          f"FAO doesn't report on are expected here - review the list to confirm "
          f"nothing real is being dropped):")
    for name in unmatched:
        print(f"  - {name}")

productivity_path = base_dir / 'FAO_Arable_Land_Productivity.csv'
df_productivity.to_csv(productivity_path, index=False)
print("Arable land productivity file saved at:", productivity_path)
