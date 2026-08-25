"""Unit tests for derive_value_kcal.py's calculation functions, against
small hand-computed synthetic inputs. No file or network I/O - main() (the
part that reads the raw FAOSTAT bulk files and writes the output CSVs) is
untested here since those files aren't checked into the repo.
"""
import pandas as pd
import pytest

import derive_value_kcal as dvk


def test_compute_kcal_per_tonne_uses_world_2022_and_divides_by_production_not_food():
    fbs = pd.DataFrame([
        {"Area": "World", "Item": "Wheat and products", "Element": "Production", "Year": 2022, "Value": 10},
        {"Area": "World", "Item": "Wheat and products", "Element": "Food supply (kcal)", "Year": 2022, "Value": 30},
        # Wrong area/year - must be ignored.
        {"Area": "Afghanistan", "Item": "Wheat and products", "Element": "Production", "Year": 2022, "Value": 999},
        {"Area": "World", "Item": "Wheat and products", "Element": "Production", "Year": 2021, "Value": 999},
    ])
    result = dvk.compute_kcal_per_tonne(fbs)
    # Food supply (kcal) is in units of 1e6 kcal, Production in 1000 t:
    # (30 * 1e6) / (10 * 1e3) = 3,000 kcal/tonne.
    assert result["Wheat and products"] == pytest.approx(3000.0)


def test_build_item_to_group_strips_leading_quote_and_maps_cpc_prefix():
    items = pd.DataFrame({"Item": ["Triticale"], "CPC Code": ["'01199"]})
    mapping = dvk.build_item_to_group(items)
    assert mapping["Triticale"] == "Cereals"


@pytest.fixture
def item_to_group():
    return {"Cucumbers and gherkins": "Vegetables", "Walnuts; in shell": "Fruits & Nuts",
            "Apricots": "Fruits & Nuts", "Jute": "Fibre & Other Crops"}


def test_resolve_fbs_item_excludes_kcal_exclude_items(item_to_group):
    assert dvk.resolve_fbs_item("Oil palm fruit", item_to_group) is None
    assert dvk.resolve_fbs_item("Barley", item_to_group) is None


def test_resolve_fbs_item_prefers_direct_match(item_to_group):
    assert dvk.resolve_fbs_item("Wheat", item_to_group) == "Wheat and products"


def test_resolve_fbs_item_falls_back_to_cpc_group(item_to_group):
    assert dvk.resolve_fbs_item("Cucumbers and gherkins", item_to_group) == "Vegetables"


def test_resolve_fbs_item_splits_fruits_and_nuts_by_keyword(item_to_group):
    assert dvk.resolve_fbs_item("Walnuts; in shell", item_to_group) == "Treenuts"
    assert dvk.resolve_fbs_item("Apricots", item_to_group) == "Fruits - Excluding Wine"


def test_resolve_fbs_item_returns_none_for_non_food_groups(item_to_group):
    # Fibre & Other Crops has no CPC_GROUP_TO_FBS entry - cotton lint, jute,
    # sisal, rubber, tobacco aren't food.
    assert dvk.resolve_fbs_item("Jute", item_to_group) is None


def test_resolve_fbs_item_returns_none_for_unmapped_item(item_to_group):
    assert dvk.resolve_fbs_item("Some Unmapped Thing", item_to_group) is None


def test_primary_crop_items_excludes_processed_derivatives_with_no_harvested_area():
    crops = pd.DataFrame([
        {"Crop": "Oil palm fruit", "AreaHarvested_ha": 100.0},
        {"Crop": "Palm oil", "AreaHarvested_ha": None},  # processed derivative, no area anywhere
        {"Crop": "Wheat", "AreaHarvested_ha": 50.0},
    ])
    primary = dvk.primary_crop_items(crops)
    assert primary == {"Oil palm fruit", "Wheat"}


def test_add_kcal_columns_and_aggregate_kcal_by_country_year():
    crops_primary = pd.DataFrame([
        {"Country": "A", "Crop": "Wheat", "Year": 2022, "Production_tons": 500},
        {"Country": "A", "Crop": "Triticale", "Year": 2022, "Production_tons": 200},
        {"Country": "B", "Crop": "Wheat", "Year": 2022, "Production_tons": 80},
    ])
    kcal_per_tonne = pd.Series({"Wheat and products": 3000.0, "Cereals - Excluding Beer": 2000.0})
    item_to_group = {"Triticale": "Cereals"}
    with_kcal = dvk.add_kcal_columns(crops_primary, kcal_per_tonne, item_to_group)
    by_country_year = dvk.aggregate_kcal_by_country_year(with_kcal)

    a_kcal = by_country_year.loc[by_country_year["Country"] == "A", "Kcal"].iloc[0]
    b_kcal = by_country_year.loc[by_country_year["Country"] == "B", "Kcal"].iloc[0]
    # A: 500*3000 (Wheat) + 200*2000 (Triticale -> Cereals fallback) = 1,900,000
    assert a_kcal == pytest.approx(1_900_000.0)
    # B: 80*3000 = 240,000
    assert b_kcal == pytest.approx(240_000.0)


def test_filter_qv_value_of_production_keeps_only_element_58_and_fixes_china_names():
    qv_raw = pd.DataFrame([
        {"Area": "China, mainland", "Item": "Wheat", "Element Code": 58, "Year": 2022, "Value": 300},
        {"Area": "TestCountry", "Item": "Wheat", "Element Code": 152, "Year": 2022, "Value": 99999},
    ])
    qv = dvk.filter_qv_value_of_production(qv_raw)
    assert len(qv) == 1
    assert qv.iloc[0]["Country"] == "China; mainland"
    assert qv.iloc[0]["Value_kUSD"] == 300


def test_fill_eu_gap_only_adds_rows_qv_has_none_for():
    crop_value_qv = pd.DataFrame([{"Country": "TestCountry", "Crop": "Wheat", "Year": 2022, "Value_kUSD": 1000}])
    eu_gap = pd.DataFrame([
        {"Country": "Germany", "Crop": "Wheat", "Year": 2022, "Value_kUSD": 777},
        {"Country": "TestCountry", "Crop": "Wheat", "Year": 2022, "Value_kUSD": 99999},  # already has QV - must not be added
    ])
    combined, added = dvk.fill_eu_gap(crop_value_qv, eu_gap)
    assert len(added) == 1
    assert added.iloc[0]["Country"] == "Germany"
    assert len(combined) == 2
    # The existing QV row for TestCountry must be untouched, not overwritten.
    tc_row = combined[combined["Country"] == "TestCountry"].iloc[0]
    assert tc_row["Value_kUSD"] == 1000


def test_compute_crop_value_per_ha_excludes_zero_and_negative_area():
    crop_value_qv = pd.DataFrame([
        {"Country": "A", "Crop": "Wheat", "Year": 2022, "Value_kUSD": 100},
        {"Country": "A", "Crop": "PalmOilLike", "Year": 2022, "Value_kUSD": 50},
    ])
    crops = pd.DataFrame([
        {"Country": "A", "Crop": "Wheat", "Year": 2022, "AreaHarvested_ha": 50},
        {"Country": "A", "Crop": "PalmOilLike", "Year": 2022, "AreaHarvested_ha": 0},
    ])
    result = dvk.compute_crop_value_per_ha(crop_value_qv, crops)
    assert result["Crop"].tolist() == ["Wheat"]
    assert result.iloc[0]["Value_per_ha"] == pytest.approx(100 * 1000 / 50)


def test_aggregate_value_basket_fallback_only_recovers_missing_country_years():
    qv_all = pd.DataFrame([
        {"Country": "A", "Item": "Cereals, primary", "Year": 2022, "Value_kUSD": 999},  # A already has item-level data
        {"Country": "B", "Item": "Cereals, primary", "Year": 2022, "Value_kUSD": 200},  # B has none
    ])
    value_item_level = pd.DataFrame([{"Country": "A", "Year": 2022, "Value_kUSD": 100}])
    recovered = dvk.aggregate_value_basket_fallback(qv_all, value_item_level)
    assert recovered["Country"].tolist() == ["B"]
    assert recovered.iloc[0]["Value_kUSD"] == 200


def test_merge_value_kcal_arable_computes_both_per_hectare_figures():
    value_by_country_year = pd.DataFrame([{"Country": "A", "Year": 2022, "Value_kUSD": 1500}])
    kcal_by_country_year = pd.DataFrame([{"Country": "A", "Year": 2022, "Kcal": 1_900_000}])
    arable = pd.DataFrame([{"Country": "A", "ArableLand_ha": 1000}])
    merged = dvk.merge_value_kcal_arable(value_by_country_year, kcal_by_country_year, arable)
    row = merged.iloc[0]
    assert row["ValuePerArableHa_USD"] == pytest.approx(1500 * 1000 / 1000)
    assert row["KcalPerArableHa"] == pytest.approx(1_900_000 / 1000)
