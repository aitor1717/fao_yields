"""
Fills part of the Global Value map's country gap - the ~46 countries FAOSTAT's
QV domain (Gross Production Value) has never once reported for in 2005-2022
(see the dashboard's own map caption). Most of that gap is
conflict-affected or currency-distorted countries (Somalia, Syria, Venezuela,
Cuba, ...) where any figure would carry false precision and is deliberately
left unfilled. This script covers the dozen where a real, if imperfect, proxy
exists: countries with ordinary statistical capacity that simply aren't in
FAOSTAT's QV domain.

Source: World Bank indicator NV.AGR.TOTL.CD ("Agriculture, forestry, and
fishing, value added, current US$"), fetched directly via the WB REST API
(same pattern as arable_land_ha.csv - no manual download):
  https://api.worldbank.org/v2/country/{iso3}/indicator/NV.AGR.TOTL.CD?format=json

This is NOT the same metric as FAOSTAT's Gross Production Value, and that
difference is real, not cosmetic: NV.AGR.TOTL.CD is a GDP value-added
figure (output minus intermediate inputs, and it bundles in forestry and
fishing, not just crops) where FAOSTAT QV is gross crop output. The two
track each other reasonably well in practice but are not interchangeable -
this is why every row this script produces is flagged IsEstimated=True
downstream and never enters the leaderboard panels (Top Value, Value per
Arable Hectare), only the two choropleth maps, with a distinct visual
treatment and an explicit hover disclaimer. Divides by the same
arable_land_ha.csv figure the rest of the dashboard uses, so the unit
(USD / arable ha) is at least dimensionally consistent with the real data
it sits next to on the map.

Country selection: twelve non-conflict, non-currency-distorted gap countries
with a large enough land area to matter on the map and ordinary WB reporting
coverage, plus a second, lower-confidence tier of conflict-affected
countries whose WB series is at least internally plausible - DR Congo,
Afghanistan, Libya, Myanmar, Sudan, Haiti. Both tiers are tagged via the
Confidence column so the dashboard can disclose the difference rather than
presenting them identically.

Sudan and Haiti passed the same plausibility check as the other four in that
tier: real WB coverage through 2022, values within the rest of the
dataset's envelope ($263-$4,311/ha), and swings that track known events
rather than contradict them (Sudan's 2018-2020 decline lines up with its
2018-2019 revolution and currency crisis; Haiti's 2021 jump lines up with
the president's July 2021 assassination and the crisis that followed) -
unlike Syria, below. South Sudan and Cuba don't clear the bar: South
Sudan's WB series stops in 2015, before 8 of the dashboard's 18 years, and
during its own civil war for the years it does cover; Cuba's stops in 2020,
exactly at its 2021 currency reunification, reinforcing the
currency-distortion concern. Somalia has zero WB data at all.

Not attempted, even in the lower-confidence tier: Syria (WB's 2022 figure
rebounds to an 11-year high mid-conflict, contradicting its well-documented
agricultural collapse) and Venezuela (WB has no data 2012-2022, only
placeholder zeros before that). Also not attempted: Taiwan (not a World
Bank member, no NV.AGR.TOTL.CD row possible).

---

Second half of this script: a real, FAOSTAT-derived KcalPerArableHa_Est for
these same sixteen countries, so the $ vs kcal map can show them too, not
just the Global Value map. The $-vs-kcal panel's kcal side (see
derive_value_kcal.py) is computed purely from each country's own crop
production tonnage (FAOSTAT's QCL domain, FAO_Crop_Yield_TableauReady.csv)
times a world-level kcal-per-tonne factor from Food Balance Sheets - it
never touches Value of Production (QV). These sixteen countries lack QV
data, but all sixteen do have real QCL production-tonnage rows (32-74 crop
rows each in 2022); the only reason they were missing from
FAO_Value_Kcal_per_ArableHa.csv is that derive_value_kcal.py's own merge is
an inner join against QV-derived value_by_country_year, dropping a
computable kcal figure for want of a value figure, not for want of real
data. This section mirrors derive_value_kcal.py's methodology (same
ITEM_TO_FBS/CPC_GROUP_TO_FBS mapping, same KCAL_EXCLUDE_ITEMS, same
Production-not-Food denominator) rather than reusing its code, since that
script's merge order isn't easily reused for a country subset without
risking the 150+ countries it already handles correctly - kept in sync by
mirroring, not importing; re-check both if either changes.

Needs the same raw FoodBalanceSheets_E_All_Data_(Normalized).csv as
derive_value_kcal.py (not checked into this repo, fetched directly here):
  https://bulks-faostat.fao.org/production/FoodBalanceSheets_E_All_Data_(Normalized).zip
"""
import io
import time
import urllib.request
import json
import zipfile
from pathlib import Path

import pandas as pd

from fao_filters import KCAL_EXCLUDE_ITEMS, WB_TO_FAO_COUNTRY

base_dir = Path(__file__).resolve().parent
data_dir = base_dir / "data"

# FAOSTAT country name -> ISO3, for the WB API call. Names already match
# FAOSTAT's own naming for all sixteen except DR Congo, whose arable_land_ha.csv
# row is only reachable via WB_TO_FAO_COUNTRY (same mapping the rest of the
# dashboard uses) - applied below before the arable-land merge.
CANDIDATES = {
    "United Arab Emirates": "ARE",
    "Guatemala": "GTM",
    "Uganda": "UGA",
    "Gabon": "GAB",
    "Papua New Guinea": "PNG",
    "Mauritania": "MRT",
    "Lesotho": "LSO",
    "Eswatini": "SWZ",
    "Liberia": "LBR",
    "Comoros": "COM",
    "Djibouti": "DJI",
    "Montenegro": "MNE",
}

# Lower-confidence tier: conflict/crisis-affected countries where a WB series
# exists and is at least internally plausible, but statistical capacity
# during the disrupted years is a real, disclosed concern (see module
# docstring - Libya's crashes track the 2019/2021 conflict escalation and
# oil blockade; Afghanistan's and Myanmar's trends are suspiciously smooth
# through 2021's Taliban takeover and coup respectively, suggesting
# extrapolation rather than fresh survey data for the most recent years).
CONFLICT_TIER = {
    "Democratic Republic of the Congo": "COD",
    "Afghanistan": "AFG",
    "Libya": "LBY",
    "Myanmar": "MMR",
    "Sudan": "SDN",
    "Haiti": "HTI",
}

WB_INDICATOR = "NV.AGR.TOTL.CD"


def fetch(candidates, confidence):
    rows = []
    for country, iso3 in candidates.items():
        url = (
            f"https://api.worldbank.org/v2/country/{iso3}/indicator/{WB_INDICATOR}"
            f"?format=json&date=2005:2022&per_page=100"
        )
        with urllib.request.urlopen(url, timeout=20) as r:
            data = json.load(r)
        entries = data[1] if len(data) > 1 and data[1] else []
        n = 0
        for e in entries:
            if e["value"] is not None:
                rows.append({
                    "Country": country, "Year": int(e["date"]), "AgValueAdded_USD": e["value"],
                    "Confidence": confidence,
                })
                n += 1
        print(f"  {country:35s} {n} years")
        time.sleep(0.2)
    return rows


print(f"Fetching World Bank {WB_INDICATOR} for {len(CANDIDATES)} stable-tier countries ...")
rows = fetch(CANDIDATES, "stable")
print(f"Fetching World Bank {WB_INDICATOR} for {len(CONFLICT_TIER)} conflict-tier countries ...")
rows += fetch(CONFLICT_TIER, "conflict-affected, lower confidence")

ag = pd.DataFrame(rows)

arable = pd.read_csv(data_dir / "arable_land_ha.csv")[["Country", "ArableLand_ha"]]
arable["Country"] = arable["Country"].replace(WB_TO_FAO_COUNTRY)
merged = ag.merge(arable, on="Country", how="inner")
dropped = set(ag["Country"]) - set(merged["Country"])
if dropped:
    print(f"WARNING: no arable_land_ha.csv row for {dropped} - dropped, cannot compute a per-hectare figure.")

merged["ValuePerArableHa_USD_Est"] = merged["AgValueAdded_USD"] / merged["ArableLand_ha"]
merged["Source"] = f"World Bank {WB_INDICATOR} (value-added proxy, not FAOSTAT QV)"

# ---------------------------------------------------------------------------
# Kcal side - real FAOSTAT data (QCL production tonnage x FBS world kcal
# factors), not a proxy. See module docstring for why this is possible at
# all. Mirrors derive_value_kcal.py's mapping exactly.
# ---------------------------------------------------------------------------
ITEM_TO_FBS = {
    "Maize (corn)": "Maize and products", "Wheat": "Wheat and products",
    "Rice": "Rice and products",
    "Oats": "Oats", "Rye": "Rye and products", "Sorghum": "Sorghum and products",
    "Millet": "Millet and products",
    "Potatoes": "Potatoes and products", "Sweet potatoes": "Sweet potatoes",
    "Yams": "Yams",
    "Groundnuts; excluding shelled": "Groundnuts",
    "Sesame seed": "Sesame seed",
    "Bananas": "Bananas", "Plantains and cooking bananas": "Plantains",
    "Apples": "Apples and products", "Grapes": "Grapes and products (excl wine)",
    "Dates": "Dates", "Pineapples": "Pineapples and products",
    "Coconuts; in shell": "Coconuts - Incl Copra",
    "Tomatoes": "Tomatoes and products", "Onions and shallots; dry (excluding dehydrated)": "Onions",
    "Olives": "Olives (including preserved)",
    "Cocoa beans": "Cocoa Beans and products", "Coffee; green": "Coffee and products",
    "Tea leaves": "Tea (including mate)",
}
CPC_GROUPS = {
    "011": "Cereals", "012": "Vegetables", "013": "Fruits & Nuts",
    "014": "Oilseeds & Oil Crops", "015": "Roots & Tubers",
    "016": "Beverage & Spice Crops", "017": "Pulses", "018": "Sugar Crops",
    "019": "Fibre & Other Crops",
}
CPC_GROUP_TO_FBS = {
    "Cereals": "Cereals - Excluding Beer", "Vegetables": "Vegetables",
    "Roots & Tubers": "Starchy Roots",
    "Beverage & Spice Crops": "Spices", "Pulses": "Pulses",
}


def resolve_fbs_item(crop_name, item_to_group):
    if crop_name in KCAL_EXCLUDE_ITEMS:
        return None
    if crop_name in ITEM_TO_FBS:
        return ITEM_TO_FBS[crop_name]
    group = item_to_group.get(crop_name)
    if group == "Fruits & Nuts":
        return "Treenuts" if "nut" in crop_name.lower() else "Fruits - Excluding Wine"
    return CPC_GROUP_TO_FBS.get(group)


all_gap_countries = list(CANDIDATES) + list(CONFLICT_TIER)

print(f"\nFetching FAOSTAT Food Balance Sheets (for the kcal-per-tonne factor table) ...")
fbs_url = "https://bulks-faostat.fao.org/production/FoodBalanceSheets_E_All_Data_(Normalized).zip"
with urllib.request.urlopen(fbs_url, timeout=120) as r:
    zip_bytes = r.read()
with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
    with zf.open("FoodBalanceSheets_E_All_Data_(Normalized).csv") as f:
        fbs = pd.read_csv(f, usecols=["Area", "Item", "Element", "Year", "Value"])
world = fbs[(fbs["Area"] == "World") & (fbs["Year"] == 2022)]
piv = world.pivot_table(index="Item", columns="Element", values="Value", aggfunc="first")
kcal_per_tonne = (piv["Food supply (kcal)"] * 1e6 / (piv["Production"] * 1e3)).dropna()
print(f"  {len(kcal_per_tonne)} FBS food-group factors computed")

print("Loading crop production tonnage for the 16 gap countries ...")
crops = pd.read_csv(data_dir / "FAO_Crop_Yield_TableauReady.csv")
crops = crops[crops["Country"].isin(all_gap_countries)]
items = pd.read_csv(data_dir / "Production_Crops_Livestock_E_ItemCodes.csv")
items["CPC_clean"] = items["CPC Code"].str.strip("'")
items["CPCGroup"] = items["CPC_clean"].str[:3].map(CPC_GROUPS)
item_to_group = dict(zip(items["Item"], items["CPCGroup"]))

primary_items = set(crops.dropna(subset=["AreaHarvested_ha"])["Crop"].unique())
crops_primary = crops[crops["Crop"].isin(primary_items)].copy()
crops_primary["FBSItem"] = crops_primary["Crop"].apply(lambda c: resolve_fbs_item(c, item_to_group))
crops_primary["KcalPerTonne"] = crops_primary["FBSItem"].map(kcal_per_tonne)
crops_primary["Kcal"] = crops_primary["Production_tons"] * crops_primary["KcalPerTonne"]

kcal_by_country_year = (
    crops_primary.dropna(subset=["Kcal"])
    .groupby(["Country", "Year"], as_index=False)["Kcal"].sum()
)
kcal_by_country_year = kcal_by_country_year.merge(arable, on="Country", how="inner")
kcal_by_country_year["KcalPerArableHa_Est"] = kcal_by_country_year["Kcal"] / kcal_by_country_year["ArableLand_ha"]

merged = merged.merge(
    kcal_by_country_year[["Country", "Year", "KcalPerArableHa_Est"]],
    on=["Country", "Year"], how="left",
)
print(f"Kcal estimate coverage: {merged['KcalPerArableHa_Est'].notna().sum()} / {len(merged)} country-years")

out = merged[[
    "Country", "Year", "ValuePerArableHa_USD_Est", "KcalPerArableHa_Est", "Source", "Confidence",
]].sort_values(["Country", "Year"])
out_path = data_dir / "FAO_ValueGapFill_WB.csv"
out.to_csv(out_path, index=False)
print(f"\nSaved {len(out)} rows to {out_path}")
print(f"Countries covered: {out['Country'].nunique()} / {len(CANDIDATES) + len(CONFLICT_TIER)}")

print("\n2022 spot check:")
print(out[out["Year"] == 2022].sort_values("ValuePerArableHa_USD_Est", ascending=False).to_string(index=False))
