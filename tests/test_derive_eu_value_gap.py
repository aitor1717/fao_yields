"""Unit tests for derive_eu_value_gap.py's calculation functions, against
small hand-computed synthetic inputs. No network I/O - main() (the Eurostat
SDMX fetch and FAOSTAT exchange-rate zip fetch) is untested here.
"""
import pandas as pd
import pytest

import derive_eu_value_gap as deg


def test_parse_eurostat_response_splits_compound_key_column():
    raw = pd.DataFrame({
        "freq,am_item,indic_agr,unit,geo\\TIME_PERIOD": ["A,AM011000,PRD_BP,MIO_EUR,DE"],
        "2022": [140],
    })
    parsed = deg.parse_eurostat_response(raw)
    assert parsed.iloc[0][["freq", "am_item", "indic_agr", "unit", "geo"]].tolist() == \
        ["A", "AM011000", "PRD_BP", "MIO_EUR", "DE"]
    assert parsed.iloc[0]["2022"] == 140


def test_filter_field_crops_keeps_only_prd_bp_mio_eur_eu_mapped_items():
    parsed = pd.DataFrame([
        {"am_item": "AM011000", "indic_agr": "PRD_BP", "unit": "MIO_EUR", "geo": "DE", "2022": 100},
        {"am_item": "AM011000", "indic_agr": "OTHER", "unit": "MIO_EUR", "geo": "DE", "2022": 999},
        {"am_item": "AM011000", "indic_agr": "PRD_BP", "unit": "MIO_NAC", "geo": "DE", "2022": 999},
        {"am_item": "AM011000", "indic_agr": "PRD_BP", "unit": "MIO_EUR", "geo": "US", "2022": 999},
        {"am_item": "AM099999", "indic_agr": "PRD_BP", "unit": "MIO_EUR", "geo": "DE", "2022": 999},
    ])
    filtered = deg.filter_field_crops(parsed)
    assert len(filtered) == 1
    assert filtered.iloc[0]["Country"] == "Germany"
    assert filtered.iloc[0]["Crop"] == "Wheat"


def test_melt_to_long_cleans_estimate_flags_and_missing_values_and_filters_year_range():
    eu = pd.DataFrame([{
        "Country": "Germany", "Crop": "Wheat",
        "2017": 999,       # out of range - dropped
        "2018": "100",
        "2019": "130 e",   # estimate flag - stripped
        "2020": ":",       # missing - dropped
    }])
    long = deg.melt_to_long(eu)
    assert sorted(long["Year"].tolist()) == [2018, 2019]
    values = dict(zip(long["Year"], long["Value_MEUR"]))
    assert values[2018] == pytest.approx(100.0)
    assert values[2019] == pytest.approx(130.0)


def test_select_eur_fx_rate_dedupes_and_filters_to_lcu_euro_annual():
    fx = pd.DataFrame([
        {"Element Code": "LCU", "Months": "Annual value", "Currency": "Euro", "Year": 2022, "Value": 0.95},
        {"Element Code": "LCU", "Months": "Annual value", "Currency": "Euro", "Year": 2022, "Value": 0.95},  # exact dupe
        {"Element Code": "SLC", "Months": "Annual value", "Currency": "Euro", "Year": 2022, "Value": 0.95},  # wrong element
        {"Element Code": "LCU", "Months": "January", "Currency": "Euro", "Year": 2022, "Value": 0.10},  # wrong months
    ])
    fx_eur = deg.select_eur_fx_rate(fx, [2022])
    assert len(fx_eur) == 1
    assert fx_eur.iloc[0]["EUR_per_USD"] == pytest.approx(0.95)


def test_select_eur_fx_rate_raises_when_eurozone_members_disagree():
    fx = pd.DataFrame([
        {"Element Code": "LCU", "Months": "Annual value", "Currency": "Euro", "Year": 2022, "Value": 0.95},
        {"Element Code": "LCU", "Months": "Annual value", "Currency": "Euro", "Year": 2022, "Value": 0.80},
    ])
    with pytest.raises(AssertionError):
        deg.select_eur_fx_rate(fx, [2022])


def test_convert_to_usd_formula():
    eu_long = pd.DataFrame([{"Country": "Germany", "Crop": "Wheat", "Year": 2022, "Value_MEUR": 140.0}])
    fx_eur = pd.DataFrame([{"Year": 2022, "EUR_per_USD": 0.95}])
    result = deg.convert_to_usd(eu_long, fx_eur)
    # USD = EUR / (EUR per USD); Value_MEUR is millions, Value_kUSD is thousands.
    assert result.iloc[0]["Value_kUSD"] == pytest.approx(140.0 * 1_000_000 / 0.95 / 1_000)
