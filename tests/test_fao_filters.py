"""Tests for the shared filtering/correction constants in fao_filters.py.

Each filter is exercised the same way pipeline.py actually applies it,
against small synthetic DataFrames with rows chosen to survive or be
dropped, rather than just inspecting the constants in isolation.
"""
import pandas as pd

from fao_filters import (
    AGGREGATE_AREAS,
    AGGREGATE_ITEMS,
    KCAL_EXCLUDE_ITEMS,
    LIVESTOCK_FALSE_POSITIVES,
    WB_TO_FAO_COUNTRY,
    livestock_pattern,
)


def apply_livestock_filter(item_names: pd.Series) -> pd.Series:
    """Mirrors pipeline.py's exact livestock-exclusion expression."""
    return item_names.isin(LIVESTOCK_FALSE_POSITIVES) | ~item_names.str.contains(livestock_pattern, regex=True)


def test_livestock_filter_drops_genuine_animal_products():
    items = pd.Series([
        "Meat, cattle", "Milk, whole fresh cow", "Eggs Primary", "Wool, greasy",
        "Cheese, whole cow milk", "Honey, natural", "Raw hides and skins of cattle",
    ])
    survives = apply_livestock_filter(items)
    assert not survives.any(), f"expected every row dropped, but kept: {items[survives].tolist()}"


def test_livestock_filter_keeps_genuine_crops():
    items = pd.Series(["Wheat", "Rice", "Maize (corn)", "Natural rubber in primary forms"])
    survives = apply_livestock_filter(items)
    assert survives.all(), f"expected every row kept, but dropped: {items[~survives].tolist()}"


def test_livestock_false_positives_are_kept_despite_matching_the_pattern():
    items = pd.Series(sorted(LIVESTOCK_FALSE_POSITIVES))
    # Every false-positive item does contain a livestock keyword as a
    # substring (that's the whole reason it needs an explicit exception) ...
    assert items.str.contains(livestock_pattern, regex=True).all()
    # ... but the combined filter still keeps every one of them.
    assert apply_livestock_filter(items).all()


def test_cassava_is_the_documented_false_positive_case():
    # "ass" in cASSava matches the livestock pattern (via "ass"/donkey) but
    # Cassava is a real staple crop, not livestock - this is the specific
    # bug the false-positive set exists to fix (see fao_filters.py's own
    # comment on this).
    assert "Cassava; fresh" in LIVESTOCK_FALSE_POSITIVES
    assert livestock_pattern.search("Cassava; fresh")
    survives = apply_livestock_filter(pd.Series(["Cassava; fresh"]))
    assert survives.all()


def apply_aggregate_item_filter(item_names: pd.Series) -> pd.Series:
    return ~item_names.isin(AGGREGATE_ITEMS)


def test_aggregate_items_are_dropped():
    items = pd.Series(["Cereals; primary", "Fruit Primary", "Vegetables Primary"])
    assert not apply_aggregate_item_filter(items).any()


def test_natural_rubber_is_not_treated_as_an_aggregate_rollup():
    # Looks like a rollup name but is a specific commodity, not a sum of
    # other items - must survive the AGGREGATE_ITEMS filter.
    assert "Natural rubber in primary forms" not in AGGREGATE_ITEMS
    survives = apply_aggregate_item_filter(pd.Series(["Natural rubber in primary forms"]))
    assert survives.all()


def apply_aggregate_area_filter(area_names: pd.Series) -> pd.Series:
    return ~area_names.isin(AGGREGATE_AREAS)


def test_aggregate_areas_are_dropped():
    areas = pd.Series(["World", "Africa", "European Union (27)", "China"])
    assert not apply_aggregate_area_filter(areas).any()


def test_real_countries_survive_the_area_filter():
    areas = pd.Series(["Afghanistan", "China; mainland", "China; Hong Kong SAR", "Brazil"])
    assert apply_aggregate_area_filter(areas).all()


def test_wb_to_fao_country_reconciles_known_naming_mismatches():
    assert WB_TO_FAO_COUNTRY["China"] == "China; mainland"
    assert WB_TO_FAO_COUNTRY["Bolivia"] == "Bolivia (Plurinational State of)"
    assert WB_TO_FAO_COUNTRY["Congo, Dem. Rep."] == "Democratic Republic of the Congo"
    assert WB_TO_FAO_COUNTRY["Egypt, Arab Rep."] == "Egypt"

    countries = pd.Series(["Bolivia", "Congo, Dem. Rep.", "Brazil"])
    reconciled = countries.replace(WB_TO_FAO_COUNTRY)
    assert reconciled.tolist() == ["Bolivia (Plurinational State of)", "Democratic Republic of the Congo", "Brazil"]


def test_kcal_exclude_items_covers_hidden_derivative_crops_but_not_maize_or_oats():
    # Maize and Oats are feed-dominated too, but documented as kept because
    # neither has a comparably large hidden-edible-derivative sibling - see
    # fao_filters.py's own comment.
    for item in ["Oil palm fruit", "Sugar cane", "Sugar beet", "Barley", "Soya beans"]:
        assert item in KCAL_EXCLUDE_ITEMS
    for item in ["Maize (corn)", "Oats"]:
        assert item not in KCAL_EXCLUDE_ITEMS
