"""Unit tests for derive_value_gap_fill.py's calculation functions, against
small hand-computed synthetic inputs. No network I/O - fetch() (World Bank
API calls) and main() are untested here.

These mirror several of derive_value_kcal.py's own tests since this script
intentionally mirrors (not imports) that logic for a 16-country subset - see
this script's own module docstring for why. Kept light here rather than
duplicating the full derive_value_kcal.py suite.
"""
import pandas as pd
import pytest

import derive_value_gap_fill as dvgf


def test_compute_value_per_arable_ha():
    ag = pd.DataFrame([{"Country": "Guatemala", "Year": 2022, "AgValueAdded_USD": 500_000_000.0}])
    arable = pd.DataFrame([{"Country": "Guatemala", "ArableLand_ha": 500}])
    result = dvgf.compute_value_per_arable_ha(ag, arable)
    assert result.iloc[0]["ValuePerArableHa_USD_Est"] == pytest.approx(1_000_000.0)
    assert "NV.AGR.TOTL.CD" in result.iloc[0]["Source"]


def test_compute_value_per_arable_ha_drops_countries_with_no_arable_land_row():
    ag = pd.DataFrame([
        {"Country": "Guatemala", "Year": 2022, "AgValueAdded_USD": 500_000_000.0},
        {"Country": "Nowhereland", "Year": 2022, "AgValueAdded_USD": 999.0},
    ])
    arable = pd.DataFrame([{"Country": "Guatemala", "ArableLand_ha": 500}])
    result = dvgf.compute_value_per_arable_ha(ag, arable)
    assert result["Country"].tolist() == ["Guatemala"]


def test_build_item_to_group_strips_leading_quote_and_maps_cpc_prefix():
    items = pd.DataFrame({"Item": ["Triticale"], "CPC Code": ["'01199"]})
    assert dvgf.build_item_to_group(items)["Triticale"] == "Cereals"


def test_resolve_fbs_item_matches_derive_value_kcals_behavior():
    item_to_group = {"Cucumbers and gherkins": "Vegetables"}
    assert dvgf.resolve_fbs_item("Oil palm fruit", item_to_group) is None
    assert dvgf.resolve_fbs_item("Wheat", item_to_group) == "Wheat and products"
    assert dvgf.resolve_fbs_item("Cucumbers and gherkins", item_to_group) == "Vegetables"


def test_compute_kcal_per_tonne_divides_by_production_not_food():
    fbs = pd.DataFrame([
        {"Area": "World", "Item": "Wheat and products", "Element": "Production", "Year": 2022, "Value": 10},
        {"Area": "World", "Item": "Wheat and products", "Element": "Food supply (kcal)", "Year": 2022, "Value": 30},
    ])
    result = dvgf.compute_kcal_per_tonne(fbs)
    assert result["Wheat and products"] == pytest.approx(3000.0)


def test_primary_crop_items_excludes_rows_with_no_harvested_area():
    crops = pd.DataFrame([
        {"Crop": "Wheat", "AreaHarvested_ha": 50.0},
        {"Crop": "Palm oil", "AreaHarvested_ha": None},
    ])
    assert dvgf.primary_crop_items(crops) == {"Wheat"}


def test_add_kcal_columns_and_aggregate_kcal_per_arable_ha():
    crops_primary = pd.DataFrame([{"Country": "Guatemala", "Crop": "Wheat", "Year": 2022, "Production_tons": 300}])
    kcal_per_tonne = pd.Series({"Wheat and products": 3000.0})
    item_to_group = {}
    with_kcal = dvgf.add_kcal_columns(crops_primary, kcal_per_tonne, item_to_group)
    arable = pd.DataFrame([{"Country": "Guatemala", "ArableLand_ha": 500}])
    result = dvgf.aggregate_kcal_per_arable_ha(with_kcal, arable)
    # Kcal = 300 * 3000 = 900,000; / 500 ha = 1,800.
    assert result.iloc[0]["KcalPerArableHa_Est"] == pytest.approx(1800.0)
