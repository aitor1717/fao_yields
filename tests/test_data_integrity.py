"""Basic data-integrity checks on the checked-in output CSVs in data/:
schema/column presence, no fully-null columns, no negative
production/area/value figures. These exist to catch silent corruption if
the CSVs are ever regenerated (this project has already hit a real case of
it: an aggfunc="first" bug silently resolved duplicate rows and corrupted
831 cells) - they don't verify the numbers are *correct*, only that
they're structurally sane.
"""
from pathlib import Path

import pandas as pd
import pytest

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# filename -> (required columns, columns that must never be negative)
CHECKS = {
    "FAO_Crop_Yield_TableauReady.csv": (
        ["Country", "Crop", "Year", "AreaHarvested_ha", "Production_tons", "Yield_tonha"],
        ["AreaHarvested_ha", "Production_tons", "Yield_tonha"],
    ),
    "FAO_Arable_Land_Productivity.csv": (
        ["Country", "Year", "Production_tons", "ArableLand_ha", "ProductionPerArableHa_tons"],
        ["Production_tons", "ArableLand_ha", "ProductionPerArableHa_tons"],
    ),
    "FAO_Crop_Value_TableauReady.csv": (
        ["Country", "Crop", "Year", "AreaHarvested_ha", "Value_kUSD", "Value_per_ha"],
        ["AreaHarvested_ha", "Value_kUSD", "Value_per_ha"],
    ),
    "FAO_EU_Crop_Value_Gap.csv": (
        ["Country", "Crop", "Year", "Value_kUSD"],
        ["Value_kUSD"],
    ),
    "FAO_ValueGapFill_WB.csv": (
        ["Country", "Year", "ValuePerArableHa_USD_Est", "KcalPerArableHa_Est", "Source", "Confidence"],
        ["ValuePerArableHa_USD_Est", "KcalPerArableHa_Est"],
    ),
    "FAO_Value_Kcal_per_ArableHa.csv": (
        ["Country", "Year", "ValuePerArableHa_USD", "KcalPerArableHa"],
        ["ValuePerArableHa_USD", "KcalPerArableHa"],
    ),
    "arable_land_ha.csv": (
        ["Country", "ArableLand_ha", "Year"],
        ["ArableLand_ha"],
    ),
    "area_data.csv": (
        ["country", "area"],
        ["area"],
    ),
}


@pytest.fixture(scope="module", params=list(CHECKS))
def loaded_csv(request):
    filename = request.param
    path = DATA_DIR / filename
    if not path.exists():
        pytest.skip(f"{filename} not checked in")
    required_columns, non_negative_columns = CHECKS[filename]
    return filename, pd.read_csv(path), required_columns, non_negative_columns


def test_required_columns_present(loaded_csv):
    filename, df, required_columns, _ = loaded_csv
    missing = set(required_columns) - set(df.columns)
    assert not missing, f"{filename} is missing columns: {missing}"


def test_has_rows(loaded_csv):
    filename, df, _, _ = loaded_csv
    assert len(df) > 0, f"{filename} has no rows"


def test_no_fully_null_columns(loaded_csv):
    filename, df, required_columns, _ = loaded_csv
    fully_null = [c for c in required_columns if df[c].isnull().all()]
    assert not fully_null, f"{filename} has fully-null columns: {fully_null}"


def test_no_negative_values(loaded_csv):
    filename, df, _, non_negative_columns = loaded_csv
    for col in non_negative_columns:
        negative = df[df[col] < 0]
        assert negative.empty, f"{filename}.{col} has {len(negative)} negative value(s)"


def test_crop_yield_matches_production_over_area():
    """Yield_tonha must equal Production_tons / AreaHarvested_ha wherever
    both are present - it's derived, never trusted from FAOSTAT's own
    "Yield" element (see README on the hg/ha vs kg/ha bug this guards
    against)."""
    df = pd.read_csv(DATA_DIR / "FAO_Crop_Yield_TableauReady.csv")
    complete = df.dropna(subset=["AreaHarvested_ha", "Production_tons", "Yield_tonha"])
    complete = complete[complete["AreaHarvested_ha"] > 0]
    expected = complete["Production_tons"] / complete["AreaHarvested_ha"]
    pd.testing.assert_series_equal(
        complete["Yield_tonha"].reset_index(drop=True),
        expected.reset_index(drop=True),
        check_names=False, rtol=1e-6,
    )


def test_no_duplicate_country_crop_year_rows():
    """pipeline.py treats duplicate (Area, Item, Year, Element) rows as a
    hard error rather than silently resolving them - the checked-in output
    should never contain any."""
    df = pd.read_csv(DATA_DIR / "FAO_Crop_Yield_TableauReady.csv")
    dupes = df.duplicated(subset=["Country", "Crop", "Year"], keep=False)
    assert not dupes.any(), f"{dupes.sum()} duplicate (Country, Crop, Year) rows found"
