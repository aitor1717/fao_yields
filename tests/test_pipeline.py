"""Tests pipeline.py's fallback branch: when the raw FAOSTAT bulk export
isn't present (the only currently-runnable path in CI - see README), it
reapplies fao_filters.py's corrections directly to a copy of the
already-produced output CSV instead of re-deriving it from raw FAO tables.

Runs the real, unmodified pipeline.py and fao_filters.py as a subprocess
against a small synthetic data/ directory (never the checked-in data/ used
by the actual dashboard), then checks the output CSVs it writes.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

CROPS_FIXTURE = pd.DataFrame([
    # Normal primary crop - exercises the yield formula.
    {"Country": "TestCountry", "Crop": "Wheat", "Year": 2020, "AreaHarvested_ha": 100, "Production_tons": 250, "Yield_tonha": None},
    # Livestock item - must be dropped.
    {"Country": "TestCountry", "Crop": "Milk, whole fresh cow", "Year": 2020, "AreaHarvested_ha": 10, "Production_tons": 500, "Yield_tonha": None},
    # Livestock false positive ("ass" in cASSava) - must survive.
    {"Country": "TestCountry", "Crop": "Cassava; fresh", "Year": 2020, "AreaHarvested_ha": 20, "Production_tons": 100, "Yield_tonha": None},
    # FAO rollup category - must be dropped.
    {"Country": "TestCountry", "Crop": "Cereals; primary", "Year": 2020, "AreaHarvested_ha": 1000, "Production_tons": 9999, "Yield_tonha": None},
    # FAO regional rollup area - must be dropped entirely.
    {"Country": "World", "Crop": "Wheat", "Year": 2020, "AreaHarvested_ha": 1e9, "Production_tons": 1e9, "Yield_tonha": None},
    # Zero harvested area - real divide-by-zero, must become blank/NaN, not inf.
    {"Country": "TestCountry", "Crop": "Rice", "Year": 2020, "AreaHarvested_ha": 0, "Production_tons": 50, "Yield_tonha": None},
    # Processed derivative (no AreaHarvested_ha anywhere) - must be excluded
    # from the arable-land-productivity production total, though it stays
    # in the crop-yield file itself (with a null Yield_tonha).
    {"Country": "TestCountry", "Crop": "Palm oil", "Year": 2020, "AreaHarvested_ha": None, "Production_tons": 1000, "Yield_tonha": None},
    # World-Bank-named country - exercises WB_TO_FAO_COUNTRY reconciliation
    # against arable_land_ha.csv below (WB says "Bolivia", FAO says
    # "Bolivia (Plurinational State of)").
    {"Country": "Bolivia (Plurinational State of)", "Crop": "Wheat", "Year": 2020, "AreaHarvested_ha": 30, "Production_tons": 60, "Yield_tonha": None},
])

ARABLE_FIXTURE = pd.DataFrame([
    {"Country": "TestCountry", "ArableLand_ha": 1000, "Year": 2023},
    {"Country": "Bolivia", "ArableLand_ha": 500, "Year": 2023},  # WB name, needs reconciliation
])


@pytest.fixture(scope="module")
def pipeline_output(tmp_path_factory):
    """Runs the real pipeline.py + fao_filters.py in an isolated temp
    directory (never the checked-in data/) and returns the two output
    DataFrames it produces."""
    work_dir = tmp_path_factory.mktemp("pipeline_fallback")
    shutil.copy(PROJECT_ROOT / "pipeline.py", work_dir / "pipeline.py")
    shutil.copy(PROJECT_ROOT / "fao_filters.py", work_dir / "fao_filters.py")
    data_dir = work_dir / "data"
    data_dir.mkdir()
    CROPS_FIXTURE.to_csv(data_dir / "FAO_Crop_Yield_TableauReady.csv", index=False)
    ARABLE_FIXTURE.to_csv(data_dir / "arable_land_ha.csv", index=False)

    # The raw bulk export is deliberately absent, so pipeline.py must take
    # its fallback branch - this is the assertion the whole test rests on.
    assert not (data_dir / "Production_Crops_Livestock_E_All_Data_(Normalized).csv").exists()

    result = subprocess.run(
        [sys.executable, "pipeline.py"], cwd=work_dir,
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"pipeline.py failed:\n{result.stdout}\n{result.stderr}"
    assert "regenerating from the checked-in" in result.stdout, \
        "pipeline.py did not report taking the fallback branch"

    crops_out = pd.read_csv(data_dir / "FAO_Crop_Yield_TableauReady.csv")
    productivity_out = pd.read_csv(data_dir / "FAO_Arable_Land_Productivity.csv")
    return crops_out, productivity_out


def test_livestock_items_are_dropped(pipeline_output):
    crops_out, _ = pipeline_output
    assert "Milk, whole fresh cow" not in crops_out["Crop"].values


def test_livestock_false_positive_survives(pipeline_output):
    crops_out, _ = pipeline_output
    assert "Cassava; fresh" in crops_out["Crop"].values


def test_aggregate_item_is_dropped(pipeline_output):
    crops_out, _ = pipeline_output
    assert "Cereals; primary" not in crops_out["Crop"].values


def test_aggregate_area_is_dropped(pipeline_output):
    crops_out, _ = pipeline_output
    assert "World" not in crops_out["Country"].values


def test_yield_is_computed_as_production_over_area_harvested(pipeline_output):
    crops_out, _ = pipeline_output
    wheat = crops_out[(crops_out["Country"] == "TestCountry") & (crops_out["Crop"] == "Wheat")].iloc[0]
    assert wheat["Yield_tonha"] == pytest.approx(250 / 100)


def test_zero_harvested_area_yields_blank_not_infinite(pipeline_output):
    crops_out, _ = pipeline_output
    rice = crops_out[(crops_out["Country"] == "TestCountry") & (crops_out["Crop"] == "Rice")].iloc[0]
    assert pd.isna(rice["Yield_tonha"])


def test_processed_derivative_excluded_from_arable_land_productivity_total(pipeline_output):
    crops_out, productivity_out = pipeline_output
    # The Palm oil row itself is retained in the crop-yield file (with a
    # null Yield_tonha, since it has no AreaHarvested_ha) ...
    assert "Palm oil" in crops_out["Crop"].values
    # ... but its 1000 tons must not be counted in TestCountry's production
    # total feeding the arable-land-productivity figure. The exclusion is on
    # a *null* AreaHarvested_ha (no area was ever reported), not a zero one:
    # Wheat (250, area 100), Cassava (100, area 20), and Rice (50, area 0 -
    # a real reported zero, not a missing value) all still count.
    row = productivity_out[(productivity_out["Country"] == "TestCountry") & (productivity_out["Year"] == 2020)].iloc[0]
    assert row["Production_tons"] == pytest.approx(250 + 100 + 50)


def test_wb_to_fao_country_reconciliation_joins_arable_land(pipeline_output):
    _, productivity_out = pipeline_output
    bolivia = productivity_out[productivity_out["Country"] == "Bolivia (Plurinational State of)"]
    assert len(bolivia) == 1
    assert bolivia.iloc[0]["ArableLand_ha"] == 500
    assert bolivia.iloc[0]["ProductionPerArableHa_tons"] == pytest.approx(60 / 500)
