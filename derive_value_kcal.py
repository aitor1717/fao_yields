"""
Derives two output CSVs from FAOSTAT's Value of Production (QV) and Food
Balance Sheets (FBS) domains:

  - FAO_Value_Kcal_per_ArableHa.csv: Country/Year totals - Gross Production
    Value and food energy (kcal) per hectare of arable land, the two lenses
    behind the dashboard's "$ vs kcal per Arable Hectare" panel: does a
    country's farmland run as an export/value business, or as calorie
    production to feed people? Uses the aggregate-basket fallback below, so
    it keeps full country coverage even after the EU's 2018 reporting change.

  - FAO_Crop_Value_TableauReady.csv: Country/Crop/Year value per harvested
    hectare, mirroring FAO_Crop_Yield_TableauReady.csv's shape - the dollar
    equivalent of Yield_tonha, used by the dashboard's per-crop panels (the
    map, Top Yields, Yield & Area by Country). This one can't use the
    aggregate-basket fallback (no per-crop breakdown in an aggregate total),
    so coverage drops for the EU's 27 member states - including the
    Netherlands - whose item-level QV reporting stops in 2018 (a clean
    cutoff across all 27, not gradual attrition; most of why Europe looks
    empty on the map in recent years). Documented on the panels' own
    captions rather than patched over.

Needs two large FAOSTAT bulk files, not checked into this repo (same pattern
as Production_Crops_Livestock_E_All_Data_(Normalized).csv - see README):
  - Value_of_Production_E_All_Data_(Normalized).csv (QV domain)
    https://bulks-faostat.fao.org/production/Value_of_Production_E_All_Data_(Normalized).zip
  - FoodBalanceSheets_E_All_Data_(Normalized).csv (FBS domain)
    https://bulks-faostat.fao.org/production/FoodBalanceSheets_E_All_Data_(Normalized).zip

Value: Element "Gross Production Value (constant 2014-2016 thousand US$)"
(code 58), not current US$ - constant prices keep real value change separate
from inflation/exchange-rate drift across 2005-2022, the same reason
Production per Arable Hectare uses a real, not nominal, unit.

Kcal: FAOSTAT has no per-crop-item calorie table, but FBS reports "Food
supply (kcal)" alongside "Production" (tonnes) for the same Item/Year at the
World level; dividing one by the other gives an FAO-derived kcal-per-
production-tonne factor for ~90 food groups. The denominator is Production,
not "Food" tonnes: dividing by Food tonnes and applying that rate to a
country's full raw harvest assumes 100% of it becomes food, which doesn't
hold - globally in 2022 only ~12% of maize and ~4% of soybean production is
direct food use (the rest is feed and industrial processing), so that
approach overstates kcal 8x for maize, 25x for soybeans, and up to 6,600x
for cottonseed. Dividing by Production instead bakes the real conversion
rate into the factor.

That still isn't enough for a handful of crops whose harvest converts almost
entirely into a different, separately-tracked FBS item rather than feed -
sugar cane/beet into "Sugar & Sweeteners", oil palm fruit into "Palm Oil",
soybeans/rapeseed/sunflower/cottonseed mostly into their "X Oil" items,
barley significantly into "Beer". For these, the raw item's own
Food/Production share is a near-zero artifact of that split (0.03% for
sugar beet, 0.02% for cottonseed), not a real measure of food use, and there
is no verified extraction rate to bridge it without inventing one -
KCAL_EXCLUDE_ITEMS drops these seven items entirely. Maize (~12% food
share) and Oats (~26%) keep their Production-based factor despite also
being feed-dominated: neither has a comparably large hidden-edible
derivative sibling, so their low share is a genuine, correctly-discounted
reflection of feed use rather than calories hiding elsewhere.

Our 176 QCL crop items are more granular than FBS's ~90 groups. Most match
an FBS group directly (ITEM_TO_FBS); items with no direct match (most
individual vegetables, fresh fruits, minor oilseeds) fall back to their CPC
3-digit group's FBS equivalent (CPC_GROUP_TO_FBS) - e.g. "Cucumbers and
gherkins" takes the broad "Vegetables" factor. "Fruits & Nuts" is split by
keyword ("nut" in the item name) since tree nuts and fresh fruit differ too
much in energy density to share one factor. "Oilseeds & Oil Crops" and
"Sugar Crops" have no CPC-group fallback, for the same hidden-derivative
reason as their named crops above. Fibre & Other Crops (cotton lint, jute,
sisal, rubber, tobacco) aren't food and are excluded from the kcal total,
same as elsewhere in this pipeline.

Both totals only include items that also appear in
FAO_Crop_Yield_TableauReady.csv with a reported AreaHarvested_ha (primary/
harvested crops) - processed derivatives (palm oil, raw sugar, wine...) are
excluded, same double-counting reason as pipeline.py.

Module structure: the calculation steps below are plain functions of
DataFrames/Series, with no file or network I/O inside them, so they can be
unit-tested against small synthetic inputs (see tests/). All file I/O,
printing, and the actual save-to-disk sequence live in main(), which
reproduces the original script's behavior exactly when run directly.
"""
from pathlib import Path

import pandas as pd

from fao_filters import KCAL_EXCLUDE_ITEMS, WB_TO_FAO_COUNTRY

base_dir = Path(__file__).resolve().parent
data_dir = base_dir / "data"
# Extracted CSVs from the two zips above go directly in data/, same as
# Production_Crops_Livestock_E_All_Data_(Normalized).csv for pipeline.py.
raw_dir = data_dir

# ---------------------------------------------------------------------------
# Direct QCL item -> FBS food-group matches (covers the great majority of
# global tonnage - major grains, oilseeds, roots, and single-item groups).
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
    # Sugar cane, Sugar beet, Barley, Soya beans, Sunflower seed, Rape or
    # colza seed, Cotton seed/Seed cotton have no entry here - see
    # KCAL_EXCLUDE_ITEMS below for why.
}

# CPC 3-digit group -> FBS fallback, for items with no direct match above.
# Mirrors the CPC grouping already used for the "produce vs field crops"
# framing elsewhere - see dashboard_app.py.
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
    # "Fibre & Other Crops" excluded - not food (cotton lint, jute, sisal,
    # rubber, tobacco have no food-energy value).
    # "Oilseeds & Oil Crops" and "Sugar Crops" have no fallback - both groups
    # are dominated by items that convert into a separately-tracked edible
    # derivative (oil, sugar) this script can't trace through - see
    # KCAL_EXCLUDE_ITEMS below. The two items in each group with a large
    # enough direct-food share to be trustworthy (Groundnuts, Sesame seed)
    # are mapped explicitly in ITEM_TO_FBS instead.
}

# FAOSTAT drops item-level Value of Production for the EU's 27 member states
# from 2018 onward (a clean cutoff across all 27 - a reporting-format change
# on the EU's side, not gradual data-quality drift), including the
# Netherlands. For country/years with no item-level rows at all, fall back
# to summing FAOSTAT's own non-overlapping "Primary" rollups (Cereals,
# primary / Vegetables and Fruit Primary / Roots and Tubers, Total / Sugar
# Crops Primary) - real underlying item totals, not an invented substitute,
# with no double-counting risk since no item-level rows exist for that
# country/year to double-count against. This basket omits oilseeds/pulses/
# beverage crops (QV has no aggregate for those), so it under-counts
# relative to item-level years - documented as a caveat on the panel.
AGGREGATE_BASKET = {"Cereals, primary", "Vegetables and Fruit Primary", "Roots and Tubers, Total", "Sugar Crops Primary"}

# Items with no reliable kcal factor: FBS's own Food/Production share (World,
# 2022) is nowhere close to 100% for any crop - some of every harvest becomes
# seed, feed, waste, or a processed product - but for most crops the item's
# own FBS row still captures the eventual food use reasonably (wheat 67%,
# rice 81%, maize 12% - all kept, see the module docstring for why maize's
# low share is trustworthy and these two below aren't). These items are
# excluded because their harvest converts almost entirely into a DIFFERENT,
# separately-tracked FBS item this script never looks up - the raw item's own
# Food/Production share is a near-zero artifact of that split, not a
# genuine measure of food use, and there's no verified extraction-rate to
# bridge it without inventing one:
#   Oil palm fruit    0.0% direct (-> Palm Oil, ~95% of FFB by mass isn't oil)
#   Sugar cane         2.2% direct (-> Sugar & Sweeteners)
#   Sugar beet         0.03% direct (-> Sugar & Sweeteners)
#   Soya beans         4.0% direct (-> Soyabean Oil + Feed)
#   Rape or colza seed 0.6% direct (-> Rape and Mustard Oil + Feed)
#   Sunflower seed     1.1% direct (-> Sunflowerseed Oil + Feed)
#   Cotton seed        0.02% direct (-> Cottonseed Oil + Feed)
# Barley (5.9% direct) is included here too - its main non-food use splits
# between genuine feed and "Beer", a separately-tracked FBS item with real
# calories this script isn't counting either. KCAL_EXCLUDE_ITEMS itself now
# lives in fao_filters.py - dashboard_app.py needs the exact same set to
# disclose the Value-side asymmetry it creates (see that module's comment).


def compute_kcal_per_tonne(fbs: pd.DataFrame, year: int = 2022) -> pd.Series:
    """FBS World kcal-per-tonne-of-PRODUCTION factor per FBS food group -
    see module docstring for why Production, not Food, is the denominator."""
    world = fbs[(fbs["Area"] == "World") & (fbs["Year"] == year)]
    piv = world.pivot_table(index="Item", columns="Element", values="Value", aggfunc="first")
    return (piv["Food supply (kcal)"] * 1e6 / (piv["Production"] * 1e3)).dropna()


def build_item_to_group(items: pd.DataFrame) -> dict:
    """QCL Item -> CPC 3-digit group name, for the CPC_GROUP_TO_FBS fallback."""
    items = items.copy()
    items["CPC_clean"] = items["CPC Code"].str.strip("'")
    items["CPCGroup"] = items["CPC_clean"].str[:3].map(CPC_GROUPS)
    return dict(zip(items["Item"], items["CPCGroup"]))


def resolve_fbs_item(crop_name: str, item_to_group: dict):
    if crop_name in KCAL_EXCLUDE_ITEMS:
        return None
    if crop_name in ITEM_TO_FBS:
        return ITEM_TO_FBS[crop_name]
    group = item_to_group.get(crop_name)
    if group == "Fruits & Nuts":
        return "Treenuts" if "nut" in crop_name.lower() else "Fruits - Excluding Wine"
    return CPC_GROUP_TO_FBS.get(group)


def primary_crop_items(crops: pd.DataFrame) -> set:
    """Crop names with at least one reported AreaHarvested_ha somewhere -
    excludes processed derivatives (palm oil, raw sugar, wine...) that never
    have their own harvested area, same scope as pipeline.py's arable-land
    productivity total."""
    return set(crops.dropna(subset=["AreaHarvested_ha"])["Crop"].unique())


def add_kcal_columns(crops_primary: pd.DataFrame, kcal_per_tonne: pd.Series, item_to_group: dict) -> pd.DataFrame:
    crops_primary = crops_primary.copy()
    crops_primary["FBSItem"] = crops_primary["Crop"].apply(lambda c: resolve_fbs_item(c, item_to_group))
    crops_primary["KcalPerTonne"] = crops_primary["FBSItem"].map(kcal_per_tonne)
    crops_primary["Kcal"] = crops_primary["Production_tons"] * crops_primary["KcalPerTonne"]
    return crops_primary


def aggregate_kcal_by_country_year(crops_primary_with_kcal: pd.DataFrame) -> pd.DataFrame:
    return (
        crops_primary_with_kcal.dropna(subset=["Kcal"])
        .groupby(["Country", "Year"], as_index=False)["Kcal"].sum()
    )


def filter_qv_value_of_production(qv_raw: pd.DataFrame) -> pd.DataFrame:
    """Keeps only Element Code 58 (Gross Production Value, constant
    2014-2016 US$) and reconciles the one QV/QCL country-naming mismatch:
    QV names China's sub-entities with commas ("China, mainland") where QCL
    uses semicolons ("China; mainland") - every other QV/QCL difference is a
    real coverage gap or a regional aggregate, not a naming mismatch."""
    qv = qv_raw[qv_raw["Element Code"] == 58].rename(columns={"Area": "Country", "Value": "Value_kUSD"}).copy()
    qv["Country"] = qv["Country"].replace({
        "China, mainland": "China; mainland",
        "China, Hong Kong SAR": "China; Hong Kong SAR",
    })
    return qv


def filter_primary_items_qv(qv_all: pd.DataFrame, primary_items: set) -> pd.DataFrame:
    return qv_all[qv_all["Item"].isin(primary_items)]


def aggregate_value_item_level(qv_items: pd.DataFrame) -> pd.DataFrame:
    return qv_items.groupby(["Country", "Year"], as_index=False)["Value_kUSD"].sum()


def build_crop_value_qv(qv_items: pd.DataFrame) -> pd.DataFrame:
    """Per-crop value (Country/Crop/Year/Value_per_ha), mirroring
    FAO_Crop_Yield_TableauReady.csv's shape - what the dashboard's per-crop
    panels need instead of the country-total figure. Item-level rows only -
    the aggregate-basket fallback gives a country total with no per-crop
    breakdown, so it's unusable here."""
    return qv_items[["Country", "Item", "Year", "Value_kUSD"]].rename(columns={"Item": "Crop"})


def fill_eu_gap(crop_value_qv: pd.DataFrame, eu_gap: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fills in what derive_eu_value_gap.py recovered from Eurostat for the
    EU's 27 member states post-2017, only where FAOSTAT QV has no row at all
    for that exact Country/Crop/Year, so this can never overwrite real QV
    data. Returns (combined, added-rows) so the caller can report coverage."""
    have_qv = set(zip(crop_value_qv["Country"], crop_value_qv["Crop"], crop_value_qv["Year"]))
    eu_gap_new = eu_gap[~eu_gap.apply(lambda r: (r["Country"], r["Crop"], r["Year"]) in have_qv, axis=1)]
    return pd.concat([crop_value_qv, eu_gap_new], ignore_index=True), eu_gap_new


def compute_crop_value_per_ha(crop_value_qv: pd.DataFrame, crops: pd.DataFrame) -> pd.DataFrame:
    crop_value = crop_value_qv.merge(
        crops[["Country", "Crop", "Year", "AreaHarvested_ha"]],
        on=["Country", "Crop", "Year"], how="inner",
    )
    crop_value = crop_value[crop_value["AreaHarvested_ha"] > 0].copy()
    crop_value["Value_per_ha"] = crop_value["Value_kUSD"] * 1000 / crop_value["AreaHarvested_ha"]
    return crop_value


def aggregate_value_basket_fallback(qv_all: pd.DataFrame, value_item_level: pd.DataFrame,
                                     aggregate_basket: set = AGGREGATE_BASKET) -> pd.DataFrame:
    """Country/years recovered via FAOSTAT's own non-overlapping "Primary"
    rollups, restricted to country/years with no item-level rows at all (see
    AGGREGATE_BASKET's own comment for why there's no double-counting risk)."""
    qv_agg = qv_all[qv_all["Item"].isin(aggregate_basket)]
    value_agg_level = qv_agg.groupby(["Country", "Year"], as_index=False)["Value_kUSD"].sum()
    has_item_level = set(zip(value_item_level["Country"], value_item_level["Year"]))
    return value_agg_level[
        ~value_agg_level.apply(lambda r: (r["Country"], r["Year"]) in has_item_level, axis=1)
    ]


def combine_value_by_country_year(value_item_level: pd.DataFrame, value_agg_only: pd.DataFrame) -> pd.DataFrame:
    return pd.concat([value_item_level, value_agg_only], ignore_index=True)


def merge_value_kcal_arable(value_by_country_year: pd.DataFrame, kcal_by_country_year: pd.DataFrame,
                             arable: pd.DataFrame) -> pd.DataFrame:
    merged = value_by_country_year.merge(kcal_by_country_year, on=["Country", "Year"], how="inner")
    merged = merged.merge(arable, on="Country", how="inner")
    merged["ValuePerArableHa_USD"] = merged["Value_kUSD"] * 1000 / merged["ArableLand_ha"]
    merged["KcalPerArableHa"] = merged["Kcal"] / merged["ArableLand_ha"]
    return merged


def main():
    print("Loading FBS World kcal-per-tonne factors ...")
    fbs_cols = ["Area", "Item", "Element", "Year", "Value"]
    fbs = pd.read_csv(
        raw_dir / "FoodBalanceSheets_E_All_Data_(Normalized).csv",
        usecols=fbs_cols,
    )
    kcal_per_tonne = compute_kcal_per_tonne(fbs)

    print("Loading crop production data ...")
    crops = pd.read_csv(data_dir / "FAO_Crop_Yield_TableauReady.csv")
    items = pd.read_csv(data_dir / "Production_Crops_Livestock_E_ItemCodes.csv")
    item_to_group = build_item_to_group(items)

    # Primary/harvested crops only (has a reported AreaHarvested_ha somewhere) -
    # excludes processed derivatives (palm oil, raw sugar, wine...), same scope
    # as the Production per Arable Hectare fix in pipeline.py.
    primary_items = primary_crop_items(crops)
    crops_primary = crops[crops["Crop"].isin(primary_items)].copy()

    crops_primary = add_kcal_columns(crops_primary, kcal_per_tonne, item_to_group)
    matched_kcal = crops_primary["KcalPerTonne"].notna()
    print(f"Kcal factor coverage: {matched_kcal.sum():,} / {len(crops_primary):,} rows "
          f"({crops_primary.loc[matched_kcal, 'Production_tons'].sum() / crops_primary['Production_tons'].sum() * 100:.1f}% of tonnage)")

    kcal_by_country_year = aggregate_kcal_by_country_year(crops_primary)

    print("Loading QV (Value of Production, constant 2014-2016 US$) ...")
    qv_cols = ["Area", "Item", "Element Code", "Year", "Value"]
    qv_raw = pd.read_csv(raw_dir / "Value_of_Production_E_All_Data_(Normalized).csv", usecols=qv_cols)
    qv_all = filter_qv_value_of_production(qv_raw)

    qv_items = filter_primary_items_qv(qv_all, primary_items)
    print(f"Value coverage: {qv_items['Item'].nunique()} of {len(primary_items)} primary crop items have QV data")
    value_item_level = aggregate_value_item_level(qv_items)

    # Per-crop value - see build_crop_value_qv's docstring for why this can
    # only use item-level rows. FAOSTAT QV alone leaves the EU's 27 member
    # states entirely absent from 2018 onward (zero rows for all 27);
    # derive_eu_value_gap.py recovers cereals, oilseeds, sugar beet, and
    # tobacco for those country/years from Eurostat (merged in below), but
    # fruit, vegetables, wine, and olives remain a documented gap for the EU
    # post-2017.
    crop_value_qv = build_crop_value_qv(qv_items)

    eu_gap_path = data_dir / "FAO_EU_Crop_Value_Gap.csv"
    if eu_gap_path.exists():
        eu_gap = pd.read_csv(eu_gap_path)
        crop_value_qv, eu_gap_new = fill_eu_gap(crop_value_qv, eu_gap)
        print(f"Eurostat rows added for EU country/crop/years FAOSTAT QV has none for: {len(eu_gap_new)} / {len(eu_gap)}")
    else:
        print("FAO_EU_Crop_Value_Gap.csv not found - run derive_eu_value_gap.py first for EU coverage.")

    crop_value = compute_crop_value_per_ha(crop_value_qv, crops)
    crop_value_path = data_dir / "FAO_Crop_Value_TableauReady.csv"
    crop_value[["Country", "Crop", "Year", "AreaHarvested_ha", "Value_kUSD", "Value_per_ha"]].to_csv(crop_value_path, index=False)
    print(f"Saved {len(crop_value)} rows to {crop_value_path}")
    covered_countries_2022 = crop_value[crop_value["Year"] == 2022]["Country"].nunique()
    print(f"Countries with per-crop value data in 2022: {covered_countries_2022}")

    value_agg_only = aggregate_value_basket_fallback(qv_all, value_item_level)
    print(f"Country-years recovered via aggregate fallback: {len(value_agg_only)}")
    value_by_country_year = combine_value_by_country_year(value_item_level, value_agg_only)

    print("Joining to arable land ...")
    arable = pd.read_csv(data_dir / "arable_land_ha.csv")[["Country", "ArableLand_ha"]]
    arable["Country"] = arable["Country"].replace(WB_TO_FAO_COUNTRY)

    merged = merge_value_kcal_arable(value_by_country_year, kcal_by_country_year, arable)

    out_path = data_dir / "FAO_Value_Kcal_per_ArableHa.csv"
    merged[["Country", "Year", "ValuePerArableHa_USD", "KcalPerArableHa"]].to_csv(out_path, index=False)
    print(f"Saved {len(merged)} rows to {out_path}")

    print("\nSpot check - 2022:")
    print(merged[merged["Year"] == 2022].sort_values("ValuePerArableHa_USD", ascending=False)
          [["Country", "ValuePerArableHa_USD", "KcalPerArableHa"]].head(15).to_string(index=False))


if __name__ == "__main__":
    main()
