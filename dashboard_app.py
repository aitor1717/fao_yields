"""
Streamlit version of the FAO Global Crop Yields dashboard, styled and arranged
to mimic the original Tableau layout: title top-left, a Global Value
choropleth (full width), Top Value line (full width, direct labels, no
legend), Top Crops by Value bar (full width, rank-graded color), a two-up row
(Crop Value vs. Cultivated Area bubble scatter + Value per Arable Hectare, in
the old "Countries Sampled" slot), a "$ vs kcal per Arable Hectare" sidenote
map, and a bottom row pairing the Value & Area chart with a notes/conclusion
callout.

Most panels are dollar-denominated (FAOSTAT's Value of Production, constant
2014-2016 USD), not tonnage - a dollar is comparable across crop types where
a ton isn't (a ton of tomatoes and a ton of wheat aren't the same thing).
This trades away some country/year coverage: Value of Production only covers
~64% of tracked crop items, and the EU's 27 member states stop reporting
item-level detail entirely after 2017 - confirmed directly, all 27 report
through 2017 and precisely zero of them do from 2018 onward, a clean cutoff
pointing to a reporting-format change on the EU's side, not a gradual
data-quality drift.

Two choropleths (Global Value, $ vs kcal per Arable Hectare) also show a
second, explicitly-flagged layer: countries FAOSTAT's Value of Production
domain has never covered get a World Bank-derived estimate instead (see
derive_value_gap_fill.py), disclosed via hover text and a legend note, never
blended into the ranked leaderboard panels. Israel is excluded throughout by
explicit request, not by any statistical rule used elsewhere in this file -
see MANUALLY_EXCLUDED_COUNTRIES below for why that's recorded plainly rather
than dressed up as a data-quality finding.

The Global Value map, Top Value, and Value & Area panels use each country's
TOTAL value per arable hectare (value_kcal / FAO_Value_Kcal_per_ArableHa.csv),
not a per-crop figure - an earlier version used the median value/ha across
each country's own reported crops, which broke badly for the EU: Eurostat's
fill-in for the post-2017 gap only covers 12 field crops (see
derive_eu_value_gap.py), so every EU country's median lost its highest-value
produce and appeared to crash 80-90% right at 2018 (confirmed directly for
Germany, France, Romania) even though nothing about farmland value actually
changed. The country-total figure doesn't have this problem - FAOSTAT's own
aggregate categories keep fruit/veg in as a lump sum even without item-level
detail - so it stays smooth across the 2017/2018 boundary. Only the
per-crop-comparison panels (Top Crops by Value, Crop Value vs. Cultivated
Area) still carry the EU's item-level gap, since a cross-country sum for a
specific crop can't be reconstructed from an aggregate category. The
Conclusion box's fixed year-over-year cohort comparison stays in tonnage
deliberately, for two reasons. First, its own 9-country cohort is itself
defined by tonnage yield (the original workbook's "top-yielding countries"
calculated field) - re-scoring a yield-defined cohort in dollars would be
answering a different question, not just re-denominating the same one (the
dashboard's Top Value panel already covers that question, with its own
value-defined leaderboard). Second, the "Global Median" side of this same
comparison - unlike the 9-country cohort, which does now have full
value_kcal coverage every year - is checked against all 178 countries in the
dashboard's own universe (after the land-area and Israel exclusions above),
and 32 of those (18.0%, re-verified 2026-07-28 - mostly conflict-affected or
lower-statistical-capacity states that have never reported Value of
Production to FAOSTAT - see derive_value_kcal.py) have no value_kcal row at
all in 2022, so switching the global comparator to dollars would silently
shrink it by roughly a fifth. See derive_value_kcal.py and each panel's own caption for
the full rationale.

Reads the corrected pipeline outputs directly - no Tableau, no extract.
Run with: streamlit run dashboard_app.py
"""
import re

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import pycountry
import streamlit as st

from fao_filters import KCAL_EXCLUDE_ITEMS, WB_TO_FAO_COUNTRY

st.set_page_config(page_title="Global Crop Yields", layout="wide", page_icon="🌾")

# --- Palette: colors sampled directly (pixel-picked) from archive/tableau/Global
# Crop Yields.png so this matches the original workbook, not an approximation of
# it. SURFACE (#333333) is the original's actual panel/chart background, sampled
# from its "Top Yields" panel, and is also the page background (one flat tone,
# no separate "card" contrast) - see .streamlit/config.toml, which sets the
# same value as the app's theme backgroundColor/secondaryBackgroundColor and
# must be kept in sync with this if it ever changes. SEQ_RAMP is the original's
# choropleth legend gradient, sampled stop-by-stop. LINE_COLORS is the bold
# 4-color green->teal set the original reuses for the Top Yields lines and bars.
SURFACE = "#333333"
TEXT = "#f5f5f5"
MUTED = "#b7b7b2"
GRID = "#4a4a4a"
SEQ_RAMP = ["#feffd9", "#f7fcc9", "#eff9b8", "#e0f4b3", "#ceeeb2", "#b5e4b3", "#95d7b7", "#77cbbc", "#5cc1c0", "#41b7c4"]
LINE_COLORS = ["#c7f296", "#94e7a8", "#51d2bb", "#27aab0"]

# Diverging scale for the $ vs kcal per Arable Hectare panel: teal (SEQ_RAMP's
# existing high end, #41b7c4) at one extreme, a new red at the other - same
# hue family as teal's own hue rotated to red (~5 degrees), but pushed
# brighter/lighter toward salmon rather than matching teal's saturation/
# lightness exactly, so it doesn't read as a dark, heavy counterweight next
# to teal's lighter tints. The pale middle stop reuses SEQ_RAMP[0] (#feffd9)
# directly - same lightness/saturation as that pale yellow, marking
# "balanced" the way the original ramp's lightest stop marked "lowest".
RED_ACCENT = "#e97063"
DIVERGING_RAMP = ["#41b7c4", "#77c2bf", "#b8dfd6", "#feffd9", "#f6cdc2", "#ee9c8d", RED_ACCENT]

# Countries/territories under this land area are excluded throughout: a
# handful of crop entries concentrated on very little land (e.g. greenhouse
# produce in a microstate) can otherwise dominate simple per-country averages
# and per-area ratios. area_data.csv is World Bank total land area in km2
# (see the project docs - it's mislabeled as "arable land" but is genuinely this).
MIN_LAND_AREA_KM2 = 1000

# Country-years reporting fewer distinct crops than this are excluded
# throughout. Combined with using the median (not the mean) below, this
# guards against one or two concentrated entries (e.g. greenhouse produce)
# dominating a country's figure.
MIN_REPORTED_CROPS = 5

# Used only by the "$ vs kcal per Arable Hectare" panel: a country/year is
# dropped if its reported crop-harvested area comes to less than this share
# of the World Bank arable-land figure that both that panel's axes divide by.
# Re-checked 2026-08-07 against the full 2022 distribution, not just Iceland
# in isolation: this is a genuine, isolated outlier, not a threshold placed
# arbitrarily near a cluster. Sorted by utilization ratio, Iceland sits at
# 0.029 (3,528 ha actually cropped against a nominal 121,000 ha "arable"
# figure) and the NEXT-lowest country in the entire dataset is Saudi Arabia
# at 0.157 - a 0.128 gap, more than four times the width of the 0.05
# threshold itself. Any cutoff between roughly 0.03 and 0.15 would exclude
# the identical single country, so this isn't a threshold tuned to fit one
# known case - it sits in the middle of an actual, isolated gap. Below this
# ratio, both of the panel's per-arable-ha figures are dividing a real,
# small numerator by a denominator that mostly isn't describing the same
# land, and the panel's log-z-score contrast (see below) reads that
# mismatch as the single most extreme "runs as a business" data point on
# the map - confirmed directly, Iceland topped that ranking by a wide
# margin before this filter. A general ratio threshold, not a hardcoded
# per-country exclusion, so it applies to whichever country the data next
# produces this pattern for, not just Iceland today.
MIN_CROP_LAND_UTILIZATION = 0.05

# Excluded by explicit request (2026-07-26), not by any statistical mechanism
# checked in this codebase - unlike every other exclusion above (land area,
# reported-crop count, land utilization), Israel's FAOSTAT data doesn't show
# a small-denominator, reporting-gap, or capacity-collapse pattern; arable
# land (271,400 ha) and reporting history are both unremarkable by the
# criteria used everywhere else here. Recorded plainly as a manual exclusion
# rather than inventing a technical justification for it.
MANUALLY_EXCLUDED_COUNTRIES = {"Israel"}

# Used only by the two ranked leaderboard panels (Top Value, Value per Arable
# Hectare) - not the choropleth maps, which color every country regardless of
# rank. A country with a tiny arable-land base turns a small, real numerator
# into an extreme per-hectare ratio that reads as "runs an intensive business"
# when it's mostly an artifact of a small denominator (same mechanism as
# MIN_CROP_LAND_UTILIZATION above, applied here to rank rather than color).
# Confirmed directly: without this floor, 2022's top of the Value per Arable
# Hectare leaderboard is Kuwait (8,000 ha), Palestine (41,900 ha), and Hong
# Kong (2,000 ha), ahead of the Netherlands (1,009,000 ha) - the country this
# dashboard's own "Top Value" narrative is built around.
#
# Re-examined 2026-08-07: unlike MIN_CROP_LAND_UTILIZATION above, this one is
# NOT sitting in a natural gap - the full distribution of arable land is
# continuous through this range (Belize/Bhutan at 100,000 ha exactly,
# Timor-Leste 111,500, Jamaica 120,000, Iceland 121,000, Lebanon 134,214, no
# break anywhere). The honest justification is a bracket, not a derived
# cutoff: 100,000 sits between the highest arable-land figure among the
# known-distorting cases (Palestine, 41,900 ha) and the lowest among
# known-legitimate high-value economies (Lebanon, 134,214 ha; Costa Rica,
# 167,133 ha - both independently corroborated as genuine high-value
# intensive producers, not artifacts). Any value in that bracket does the
# same job; 100,000 isn't a statistically special point within it, and this
# comment says so rather than implying a precision the data doesn't support.
MIN_ARABLE_LAND_HA = 100_000

TOP_N_BARS = 10

ISO3_OVERRIDES = {
    "Bolivia (Plurinational State of)": "BOL",
    "China; Hong Kong SAR": "HKG", "China; Macao SAR": "MAC",
    "China; Taiwan Province of": "TWN", "China; mainland": "CHN",
    "Democratic Republic of the Congo": "COD",
    "Iran (Islamic Republic of)": "IRN",
    "Micronesia (Federated States of)": "FSM",
    "Netherlands (Kingdom of the)": "NLD",
    "Venezuela (Bolivarian Republic of)": "VEN",
    # pycountry.search_fuzzy("Republic of Korea") incorrectly matches North
    # Korea's official record ("Korea, Democratic People's Republic of") as
    # its top hit, so both Koreas resolved to PRK - South Korea's shape got
    # no data on the map while PRK showed whichever row happened to win.
    "Republic of Korea": "KOR",
    "Democratic People's Republic of Korea": "PRK",
    # Same bug, same fix: search_fuzzy("Niger") returns Nigeria as its top
    # hit (Niger's own record is second) - confirmed directly while doing a
    # general pass for this exact class of bug. Both resolved to NGA, so
    # Niger's shape showed no data on every choropleth in this dashboard.
    "Niger": "NER",
    "Nigeria": "NGA",
}

DISPLAY_NAME_OVERRIDES = {
    "Netherlands (Kingdom of the)": "Netherlands",
    "United Kingdom of Great Britain and Northern Ireland": "UK",
    "United States of America": "USA",
    "Bolivia (Plurinational State of)": "Bolivia",
    "Iran (Islamic Republic of)": "Iran",
    "Venezuela (Bolivarian Republic of)": "Venezuela",
    "Democratic Republic of the Congo": "DR Congo",
    "United Republic of Tanzania": "Tanzania",
    "Republic of Korea": "South Korea",
    "Democratic People's Republic of Korea": "North Korea",
    "Lao People's Democratic Republic": "Laos",
    "Syrian Arab Republic": "Syria",
    "Micronesia (Federated States of)": "Micronesia",
    "China; mainland": "China",
    # Kept distinct from mainland "China" (a different FAOSTAT reporting area,
    # not a subset of it) - "China; Hong Kong SAR" shortened for readability
    # without conflating the two.
    "China; Hong Kong SAR": "Hong Kong",
}

# Fixed historical cohort from the original workbook's calculated field - not
# the same as the dynamic top-yield countries plotted in the Top Yields panel
# above. Kept as its own benchmark so the Conclusion figures are reproducible
# across years, independent of who currently tops the ranking.
TOP_YIELD_COUNTRIES = [
    "Netherlands (Kingdom of the)", "Austria",
    "United Kingdom of Great Britain and Northern Ireland",
    "Belgium", "Spain", "Türkiye", "China; mainland", "Brazil", "India",
]


# @st.cache_data here is load-bearing, not an optimization: search_fuzzy() is
# an O(n) fuzzy match against ~250 country names, and to_iso3 is applied to
# 189K+ crop rows. The decorator memoizes per distinct country name so the
# fuzzy match only actually runs ~250 times; without it this call hangs for
# well over 10 minutes at 100% CPU. Don't remove it "for simplicity".
@st.cache_data
def to_iso3(name: str):
    if name in ISO3_OVERRIDES:
        return ISO3_OVERRIDES[name]
    try:
        return pycountry.countries.search_fuzzy(name)[0].alpha_3
    except LookupError:
        return None


def simplify_crop_name(name: str) -> str:
    """Drop FAOSTAT classification jargon ('n.e.c.', processing-method
    parentheticals) that adds no information on a chart label."""
    name = re.sub(r"\s*;?\s*n\.e\.c\.?", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*\(centrifugal only\)", "", name, flags=re.IGNORECASE)
    return name.replace(";", ",").strip().rstrip(",").strip()


@st.cache_data
def load_data():
    crops = pd.read_csv("FAO_Crop_Yield_TableauReady.csv")
    value_kcal = pd.read_csv("FAO_Value_Kcal_per_ArableHa.csv")
    crop_value = pd.read_csv("FAO_Crop_Value_TableauReady.csv")

    land_area = pd.read_csv("area_data.csv").rename(columns={"country": "Country", "area": "LandArea_km2"})
    land_area["Country"] = land_area["Country"].replace(WB_TO_FAO_COUNTRY)
    small_countries = set(land_area.loc[land_area["LandArea_km2"] < MIN_LAND_AREA_KM2, "Country"])
    excluded_countries = small_countries | MANUALLY_EXCLUDED_COUNTRIES
    crops = crops[~crops["Country"].isin(excluded_countries)]
    value_kcal = value_kcal[~value_kcal["Country"].isin(excluded_countries)]
    crop_value = crop_value[~crop_value["Country"].isin(excluded_countries)]

    # Per (Country, Year), require at least MIN_REPORTED_CROPS distinct crops
    # with actual yield data. Applied to every panel that reads Yield_tonha.
    reported = (
        crops.dropna(subset=["Yield_tonha"])
        .groupby(["Country", "Year"])["Crop"].nunique()
        .rename("ReportedCrops").reset_index()
    )
    crops = crops.merge(reported, on=["Country", "Year"], how="left")
    crops = crops[crops["ReportedCrops"].fillna(0) >= MIN_REPORTED_CROPS].drop(columns="ReportedCrops")

    # Same threshold, applied to crop_value's own (much sparser - QV covers
    # ~64% of items, and drops to country-total-only for the EU's 27 member
    # states after 2017) coverage, not reused from the yield-side ReportedCrops above.
    reported_value = (
        crop_value.groupby(["Country", "Year"])["Crop"].nunique()
        .rename("ReportedCrops").reset_index()
    )
    crop_value = crop_value.merge(reported_value, on=["Country", "Year"], how="left")
    crop_value = crop_value[crop_value["ReportedCrops"].fillna(0) >= MIN_REPORTED_CROPS].drop(columns="ReportedCrops")

    crops["DisplayCountry"] = crops["Country"].replace(DISPLAY_NAME_OVERRIDES)
    crops["DisplayCrop"] = crops["Crop"].apply(simplify_crop_name)
    crops["ISO3"] = crops["Country"].apply(to_iso3)

    value_kcal["DisplayCountry"] = value_kcal["Country"].replace(DISPLAY_NAME_OVERRIDES)
    value_kcal["ISO3"] = value_kcal["Country"].apply(to_iso3)
    # Arable land per country, carried through only so the "$ vs kcal" panel
    # can compute each country/year's actual crop-land utilization ratio
    # (see MIN_CROP_LAND_UTILIZATION above) - not used by any other panel.
    arable = pd.read_csv("arable_land_ha.csv")[["Country", "ArableLand_ha"]]
    arable["Country"] = arable["Country"].replace(WB_TO_FAO_COUNTRY)
    value_kcal = value_kcal.merge(arable, on="Country", how="left")

    crop_value["DisplayCountry"] = crop_value["Country"].replace(DISPLAY_NAME_OVERRIDES)
    crop_value["DisplayCrop"] = crop_value["Crop"].apply(simplify_crop_name)
    crop_value["ISO3"] = crop_value["Country"].apply(to_iso3)

    # Estimated fill for the two choropleth maps only (see
    # derive_value_gap_fill.py) - World Bank agriculture-value-added proxy for
    # the $ side, plus a real FAOSTAT-derived kcal side (these countries' own
    # QCL production tonnage run through the same kcal-per-tonne factors used
    # everywhere else) for the $-vs-kcal map. Deliberately kept as its own
    # table rather than merged into value_kcal: it must never reach the
    # leaderboard panels (Top Value, Value per Arable Hectare), which stay
    # FAOSTAT-only. Same MIN_ARABLE_LAND_HA floor as the leaderboards, applied
    # here too - an earlier pass without it put UAE's estimate at $75,203/ha,
    # ~3.6x the real dataset's actual top value (Costa Rica, $20,778/ha), the
    # same small-denominator distortion the leaderboard floor exists to
    # prevent.
    est_fill = pd.read_csv("FAO_ValueGapFill_WB.csv").merge(arable, on="Country", how="left")
    est_fill = est_fill[est_fill["ArableLand_ha"].fillna(0) >= MIN_ARABLE_LAND_HA]
    est_fill["DisplayCountry"] = est_fill["Country"].replace(DISPLAY_NAME_OVERRIDES)
    est_fill["ISO3"] = est_fill["Country"].apply(to_iso3)

    return crops, value_kcal, crop_value, est_fill


crops, value_kcal, crop_value, est_fill = load_data()

# Conclusion figures - computed early so both the top-of-page teaser and the
# bottom callout box can use them without duplicating the calculation. Median,
# not mean: a country's per-crop yields are right-skewed (a couple of very
# high-yield crops, e.g. greenhouse produce, otherwise dominate the figure -
# confirmed directly: Iceland's mean yield was 144 t/ha vs a median of 16).
top_2012 = crops[(crops["Country"].isin(TOP_YIELD_COUNTRIES)) & (crops["Year"] == 2012)]["Yield_tonha"].median()
top_2022 = crops[(crops["Country"].isin(TOP_YIELD_COUNTRIES)) & (crops["Year"] == 2022)]["Yield_tonha"].median()
glob_2012 = crops[crops["Year"] == 2012]["Yield_tonha"].median()
glob_2022 = crops[crops["Year"] == 2022]["Yield_tonha"].median()
top_pct = (top_2022 / top_2012 - 1) * 100
glob_pct = (glob_2022 / glob_2012 - 1) * 100

st.markdown(
    f"""
    <style>
    .block-container {{ padding-top: 3rem; }}
    header[data-testid="stHeader"] {{ display: none; }}
    .panel-title {{
        font-size: 13px; font-weight: 600; color: {MUTED}; margin: 4px 0 8px 0;
    }}
    /* Caption hierarchy: cap-primary is the panel's one load-bearing sentence
       (what it means), cap-secondary is optional methodology/caveat detail
       underneath - smaller and muted, flush-left (no indent/rule - size and
       color alone carry the hierarchy) so the two tiers are visually
       distinct rather than both rendered as uniform st.caption text (the
       original flat treatment had no hierarchy at all). */
    .cap-primary {{ font-size: 14px; color: {TEXT}; line-height: 1.55; margin: 6px 0 2px 0; }}
    .cap-secondary {{ font-size: 11.5px; color: {MUTED}; line-height: 1.55; margin: 3px 0 2px 0; }}
    .callout-box b {{ color: #ffffff; }}
    .callout-box .insight {{ color: {MUTED}; font-size: 12.5px; margin-top: 10px; display:block; }}
    .callout-box .notes {{ color: {MUTED}; font-size: 12px; line-height: 1.6; margin-top: 4px; display:block; }}
    div[data-testid="stMetric"] {{ background-color: transparent; padding: 0; }}
    div[data-testid="stSelectbox"] div[role="group"] {{
        background-color: transparent !important; border: 1px solid {GRID} !important;
        border-radius: 4px;
    }}
    div[data-testid="stSelectbox"] input {{ background-color: transparent !important; }}
    /* Year slider: a small, subdued round thumb - no pill background, no
       shadow/glow, no hover scale, no bold fill. Solid muted teal (a flat
       fill, not transparent) so it reads as a quiet control rather than a
       call-to-action without depending on whatever sits behind it. Still
       shows the year value via the native thumb-value label, centered
       precisely in the circle (the inner <p> tag's default margin/line-
       height otherwise pushes it off-center at this small size). Track
       thinned to match. Tick labels (min/max year) keep the same muted tone
       as every other axis/caption label on the page instead of Streamlit's
       default.
    */
    div[data-testid="stSlider"] div[data-testid="stSliderThumbValue"] {{
        width: 28px; height: 28px; min-width: 28px; padding: 0; border-radius: 50%;
        display: flex; align-items: center; justify-content: center;
        background-color: #448a7e; border: none; box-shadow: none;
        font-size: 10px; font-weight: 500; color: {TEXT} !important;
    }}
    div[data-testid="stSlider"] div[data-testid="stSliderThumbValue"] p {{
        color: {TEXT} !important; margin: 0; line-height: 1; text-align: center;
    }}
    div[data-testid="stSlider"] > div > div > div {{ height: 3px; }}
    div[data-testid="stSliderTickBar"] p {{ color: {MUTED}; font-size: 12px; }}
    /* Fixed-height bordered containers (the aligned two-up rows) shouldn't
       show a scrollbar even on marginal overflow - this is a presentational
       dashboard, not a scrolling one, and Linux Firefox renders a visible
       scrollbar track by default (unlike overlay-scrollbar platforms). */
    div[data-testid="stVerticalBlock"] {{ scrollbar-width: none; }}
    div[data-testid="stVerticalBlock"]::-webkit-scrollbar {{ display: none; }}
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Header (title, intro caption, year control) gets its own bordered frame,
# matching every panel below - previously this sat directly on the page
# background, the only unframed content on the page.
# ---------------------------------------------------------------------------
with st.container(border=True):
    st.markdown(
        f"<div style='font-size:28px; font-weight:700; color:{TEXT}; margin-bottom:0;'>Global Crop Yields</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<div class='cap-secondary' style='margin-top:2px;'>This dashboard shows the value and food "
        "energy each country gets from its farmland, and how these have changed over time. Most "
        "figures use US dollars, not tonnes. A dollar of any crop is comparable; a tonne of one crop "
        "is not equal to a tonne of another. All figures come from FAOSTAT and World Bank data. "
        "No figure is invented or entered by hand.</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"<div class='cap-primary'>FAO/World Bank data, {int(crops['Year'].min())}–{int(crops['Year'].max())}. "
        f"Since 2012, a fixed group of historically top-yielding countries has pulled further ahead "
        f"of the global median ({top_pct:+.1f}% vs {glob_pct:+.1f}%).</div>",
        unsafe_allow_html=True,
    )

# Year control - deliberately outside the header's bordered frame and
# unframed itself, so it reads as a page-level control rather than part of
# the title card. A slider (matching the original workbook), since it's the
# one control that's genuinely global (every panel reads it). Years are a
# contiguous range (2005-2022, confirmed directly), so a plain slider works
# without needing st.select_slider's explicit option list.
years = sorted(crops["Year"].unique().tolist())
year = st.slider("Year", min_value=years[0], max_value=years[-1], value=2022 if 2022 in years else years[-1])

# ---------------------------------------------------------------------------
# Global Value choropleth (full width) - value per ARABLE hectare, country
# total, not a per-crop median. An earlier version of this panel used the
# median value/ha across each country's own reported crops (like Top Yields
# does for tonnage) - dollars fixed the cross-crop commensurability problem
# tonnage had, but the median was still fragile to *which* crops a country
# happened to report in a given year. That fragility broke badly once the
# EU's 27 member states stopped reporting item-level Value of Production
# after 2017 (see derive_eu_value_gap.py): the Eurostat fill-in only covers
# 12 field crops, so every EU country's median lost its highest-value produce
# and appeared to crash 80-90% right at 2018 - confirmed directly (Germany,
# France, Romania) - with no such cliff in this country-total metric for the
# same countries/years. Switched to the country-total figure already used by
# the "Value per Arable Hectare" bar chart and the "$ vs kcal" map below,
# which sidesteps the problem entirely: FAOSTAT's own aggregate categories
# (Cereals primary, Vegetables and Fruit Primary, etc.) keep fruit/veg in the
# total as a lump sum even where item-level detail is missing.
with st.container(border=True):
    st.markdown(f"<div class='panel-title'>Global Value {year}</div>", unsafe_allow_html=True)
    map_df = value_kcal[value_kcal["Year"] == year].dropna(subset=["ISO3", "ValuePerArableHa_USD"])[
        ["ISO3", "DisplayCountry", "ValuePerArableHa_USD"]
    ].copy()
    # Color on a log scale rather than the raw value: value per hectare is
    # heavily right-skewed, so a linear scale spends nearly all its range on
    # gaps between the top few countries and leaves the rest of the map visually
    # flat. Only the color *mapping* changes here - the underlying values
    # (and the hover tooltip) are the real figures.
    map_df["ColorValue"] = np.log10(map_df["ValuePerArableHa_USD"])
    cmin, cmax = map_df["ColorValue"].min(), map_df["ColorValue"].max()
    tick_vals = [500, 1000, 2500, 5000, 10000, 20000]
    fig = px.choropleth(
        map_df, locations="ISO3", color="ColorValue", hover_name="DisplayCountry",
        color_continuous_scale=SEQ_RAMP, custom_data=["ValuePerArableHa_USD"],
        range_color=(cmin, cmax),
    )
    fig.update_traces(
        hovertemplate="<b>%{hovertext}</b><br>Value: $%{customdata[0]:,.0f}/arable ha<extra></extra>",
        marker_line_width=0,  # the choropleth trace draws its own polygon borders,
    )  # separate from geo.showcountries - both needed off to fully remove lines

    # Estimated fill (see derive_value_gap_fill.py) - a second trace, not
    # blended into the real one, so it can carry its own opacity/border and
    # hover disclaimer. No visual distinction (border/opacity) from the real
    # trace by design - the goal is one uniform-looking map; disclosure lives
    # in the hover text, the legend note below, and the caption, not in a
    # visibly different fill. Color clipped to the real data's own cmin/cmax
    # so it can't stretch the shared legend past what the real values need.
    est_map = est_fill[est_fill["Year"] == year].dropna(subset=["ISO3", "ValuePerArableHa_USD_Est"])[
        ["ISO3", "DisplayCountry", "ValuePerArableHa_USD_Est", "Confidence"]
    ].copy()
    if not est_map.empty:
        est_map["ColorValue"] = np.log10(est_map["ValuePerArableHa_USD_Est"]).clip(cmin, cmax)
        fig.add_trace(go.Choropleth(
            locations=est_map["ISO3"], z=est_map["ColorValue"], zmin=cmin, zmax=cmax,
            coloraxis="coloraxis",
            customdata=est_map[["DisplayCountry", "ValuePerArableHa_USD_Est", "Confidence"]],
            hovertemplate="<b>%{customdata[0]}</b><br>Estimated: $%{customdata[1]:,.0f}/arable ha"
                          "<br><i>World Bank proxy, not FAOSTAT (%{customdata[2]})</i><extra></extra>",
            marker_line_width=0,
        ))
    fig.update_layout(
        # White basemap (matching the original) rather than dark-on-dark. No
        # country borders or coastline outlines - countries are told apart by
        # color contrast alone. Light gray (not white) for countries with no
        # yield data, distinct from the white ocean and the colored countries;
        # showland must be explicit or the land layer doesn't render at all.
        # lataxis/lonaxis crop tightly to the populated landmass extent - this is
        # deliberately manual rather than fitbounds="locations", which pulled in
        # a disconnected, badly-distorted sliver of Antarctica at the edge.
        # Revised 2026-07-29 after a pixel-measurement pass (rendered the geo
        # trace standalone at the exact 1408x630 container size, then measured
        # the non-white bounding box in each direction): the previous -176/178
        # lon range still clipped Alaska and Chukotka mid-shape (8-31% of the
        # very edge columns were land, not ocean) while leaving 25px of dead
        # white margin top/bottom. A -176/178 seam is wide enough to reach past
        # Alaska's Aleutian tail and Chukotka's peninsula tip, both of which
        # cross close to the antimeridian - any simple rectangular crop that
        # tries to include them clips one or the other. Narrowing to -160/160
        # (excludes the Aleutian tail and the Chukotka peninsula tip entirely,
        # keeps all of mainland Alaska and mainland Russia) removes the clip,
        # but also changes the lon:lat aspect ratio Plotly fits the frame to,
        # which pushed Greenland's tip and Antarctica-adjacent content to the
        # opposite extreme (touching row 0/629 - confirmed by the same
        # measurement). Extending lataxis from -56 to -58 and scale from 1.13
        # to 0.98 rebalances this: measured margins are now a consistent
        # 6-9px on every side, no clipping, no dead space - not just "picked
        # to look right," each number here is the result of that same
        # measure-adjust-remeasure loop, not a first guess.
        geo=dict(
            bgcolor="white", lakecolor="white", landcolor="#dcdcdc", showframe=False,
            showland=True, showcountries=False, showcoastlines=False,
            lataxis=dict(range=[-58, 78]), lonaxis=dict(range=[-160, 160]),
            projection=dict(scale=0.98, rotation=dict(lon=0)), center=dict(lon=0, lat=11),
        ),
        paper_bgcolor="white", font_color="#333333", autosize=True,
        margin=dict(l=0, r=0, t=0, b=0), height=630,
        # No drag/zoom interaction: this map is presentational, and Plotly's geo
        # zoom/autoscale doesn't respect the manual lataxis/lonaxis crop above -
        # interacting with it re-fits to a wider default that clips Alaska.
        dragmode=False,
        coloraxis_showscale=False,  # legend is drawn below as a plain HTML footnote instead -
    )  # Plotly's own colorbar sat on top of the map itself, illegible against whatever landmass was under it
    st.plotly_chart(fig, width='stretch', config=dict(displayModeBar=False, scrollZoom=False, doubleClick=False))

    # Footnote-style legend: same gradient, same tick values, same bar size as
    # the Plotly colorbar it replaces, but rendered as plain HTML below the map
    # (dark theme, muted text) instead of overlaid on the white canvas.
    tick_labels = {v: f"${v // 1000}K" if v >= 1000 else f"${v}" for v in tick_vals}
    ticks_html = "".join(
        f"<span style='position:absolute; left:{(np.log10(v) - cmin) / (cmax - cmin) * 100:.1f}%; "
        f"transform:translateX(-50%);'>{tick_labels[v]}</span>"
        for v in tick_vals if cmin <= np.log10(v) <= cmax
    )
    est_swatch = (
        f"""<div style='margin-top:8px;'>
            {est_map["DisplayCountry"].nunique() if not est_map.empty else 0} of the countries shown
            are estimated (World Bank proxy, not FAOSTAT). Hover a country to see which.
        </div>"""
        if not est_map.empty else ""
    )
    st.markdown(
        f"""
        <div style='margin-top:8px; font-size:12px; color:{MUTED};'>
            Value ($/arable ha)
            <div style='width:520px; height:12px; margin-top:4px; border-radius:2px;
                         background:linear-gradient(to right, {", ".join(SEQ_RAMP)});'></div>
            <div style='position:relative; width:520px; height:14px; margin-top:2px; font-size:11px;'>
                {ticks_html}
            </div>
            {est_swatch}
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        "<div class='cap-primary'>This map shows each country's total crop production value "
        "per hectare of arable land.</div>"
        "<div class='cap-secondary'>This is a country total, not a per-crop figure. It does not "
        "change with which crops a country happens to report in a given year.</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<div class='cap-secondary'>FAOSTAT has no item-level Value of Production data for the EU "
        "after 2017. This map uses each EU country's total value instead, which FAOSTAT still "
        "reports.</div>",
        unsafe_allow_html=True,
    )
    if not est_map.empty:
        n_conflict = int((est_map["Confidence"] == "conflict-affected, lower confidence").sum())
        st.markdown(
            "<div class='cap-secondary'>Hover a country to see whether its value is FAOSTAT-"
            f"reported or estimated. FAOSTAT has no Value of Production figure for "
            f"{est_map['DisplayCountry'].nunique()} of the countries shown. Their estimate uses "
            "World Bank agricultural value-added data instead, a related but different measure. "
            f"{n_conflict} of these are conflict-affected countries; their estimate carries extra "
            "uncertainty, noted in the hover text. The $ vs kcal map below shows the same "
            "countries. Only the Top Value and Value per Arable Hectare rankings exclude them, "
            "since those rank FAOSTAT-reported figures only.</div>",
            unsafe_allow_html=True,
        )
    st.markdown(
        "<div class='cap-secondary'>The color scale is logarithmic. Value per hectare is heavily "
        "skewed. A few countries are far above the rest.</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Top Yields (full width, direct end-of-line labels, no legend)
# ---------------------------------------------------------------------------
with st.container(border=True):
    st.markdown("<div class='panel-title'>Top Value</div>", unsafe_allow_html=True)
    # Country-total value per arable ha (same value_kcal metric as the map
    # above and the "Value per Arable Hectare" bar chart), not a per-crop
    # median - see the note above the Global Value map for why the median
    # version of this panel was replaced (EU countries' median crashed at
    # 2018 purely from losing crop coverage, not real value).
    latest_year = value_kcal["Year"].max()
    n_top = 6
    # MIN_ARABLE_LAND_HA excludes tiny-arable-land economies (Kuwait, Palestine,
    # Hong Kong) whose per-hectare ratio is inflated by a small denominator,
    # not genuinely intensive production - see its definition above.
    top_countries_now = (
        value_kcal[(value_kcal["Year"] == latest_year) & (value_kcal["ArableLand_ha"] >= MIN_ARABLE_LAND_HA)]
        .groupby("DisplayCountry")["ValuePerArableHa_USD"].sum()
        .sort_values(ascending=False).head(n_top).index.tolist()
    )
    trend = value_kcal[
        (value_kcal["DisplayCountry"].isin(top_countries_now)) & (value_kcal["Year"] >= 2012)
    ][["DisplayCountry", "Year", "ValuePerArableHa_USD"]].rename(columns={"ValuePerArableHa_USD": "Value_per_ha"})

    # Color by rank along the same pale-yellow -> teal ramp the choropleth
    # uses, so "more teal" consistently means "higher value" across the
    # whole dashboard, rather than an arbitrary rotation through a palette.
    n = len(top_countries_now)
    rank_colors = px.colors.sample_colorscale(SEQ_RAMP, [i / (n - 1) for i in range(n)]) if n > 1 else SEQ_RAMP[-1:]

    value_range = trend["Value_per_ha"].max() - trend["Value_per_ha"].min()
    min_label_gap = value_range * 0.09
    label_y = sorted(
        (trend[trend["DisplayCountry"] == c].sort_values("Year")["Value_per_ha"].iloc[-1], c)
        for c in top_countries_now
    )
    for j in range(1, len(label_y)):
        y, c = label_y[j]
        prev_y = label_y[j - 1][0]
        if y - prev_y < min_label_gap:
            label_y[j] = (prev_y + min_label_gap, c)
    label_y = dict((c, y) for y, c in label_y)

    fig = go.Figure()
    for i, c in enumerate(top_countries_now):
        sub = trend[trend["DisplayCountry"] == c].sort_values("Year")
        color = rank_colors[n - 1 - i]  # rank 0 = highest value -> darkest/teal-est
        fig.add_trace(go.Scatter(
            x=sub["Year"], y=sub["Value_per_ha"], mode="lines", name=c,
            line=dict(color=color, width=2, shape="spline"), showlegend=False,
        ))
        # Label placed at a decluttered y (label_y), not necessarily the
        # exact last data point, so labels stay readable when values cluster.
        fig.add_annotation(
            x=sub["Year"].iloc[-1], y=label_y[c], text=f"  {c}",
            showarrow=False, xanchor="left", font=dict(color=color, size=12),
        )
    fig.update_layout(
        plot_bgcolor=SURFACE, paper_bgcolor=SURFACE, font_color=TEXT,
        margin=dict(l=10, r=110, t=10, b=10), height=250,
        yaxis_title="Value ($ / arable ha)",
    )
    fig.update_xaxes(gridcolor=GRID, showgrid=False, fixedrange=True)
    fig.update_yaxes(showgrid=False, fixedrange=True)
    st.plotly_chart(fig, width='stretch', config=dict(displayModeBar=False, scrollZoom=False, doubleClick=False))
    st.markdown(
        f"<div class='cap-primary'>The top six countries by value per arable hectare are "
        f"{', '.join(top_countries_now)}.</div>"
        "<div class='cap-secondary'>Each one runs intensive, high-value production on a small land "
        "base. This is often greenhouse horticulture or export crops, not high yields spread over "
        "a large area.</div>"
        f"<div class='cap-secondary'>This chart excludes countries with under {MIN_ARABLE_LAND_HA:,} ha "
        "of arable land, such as Kuwait, Palestine, and Hong Kong. Their high ratio comes from a "
        "small denominator, not from genuinely intensive production.</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Top Crops bar (full width, rank-graded color to match the original)
# ---------------------------------------------------------------------------
with st.container(border=True):
    st.markdown(f"<div class='panel-title'>Top Crops by Value, {year}</div>", unsafe_allow_html=True)
    year_crop_value_all = crop_value[crop_value["Year"] == year]
    top_crops = (
        year_crop_value_all.groupby("DisplayCrop", as_index=False)["Value_kUSD"].sum()
        .sort_values("Value_kUSD", ascending=False).head(TOP_N_BARS)
    )
    top_crops["Value_USD"] = top_crops["Value_kUSD"] * 1000
    top_crops = top_crops.sort_values("Value_USD")
    rank_colors = px.colors.sample_colorscale(LINE_COLORS, [i / (len(top_crops) - 1) for i in range(len(top_crops))]) if len(top_crops) > 1 else LINE_COLORS[:1]

    fig = go.Figure(go.Bar(
        x=top_crops["Value_USD"], y=top_crops["DisplayCrop"], orientation="h",
        marker=dict(color=rank_colors),
    ))
    fig.update_layout(
        plot_bgcolor=SURFACE, paper_bgcolor=SURFACE, font_color=TEXT,
        margin=dict(l=10, r=10, t=10, b=10), height=340,
        xaxis_title="Value (USD)",
    )
    fig.update_xaxes(gridcolor=GRID, fixedrange=True)
    fig.update_yaxes(gridcolor=GRID, fixedrange=True)
    st.plotly_chart(fig, width='stretch', config=dict(displayModeBar=False, scrollZoom=False, doubleClick=False))
    st.markdown(
        f"<div class='cap-primary'>This chart ranks the top {TOP_N_BARS} crops by total value, summed "
        "across countries with item-level data.</div>"
        "<div class='cap-secondary'>This is not the same ranking as by tonnage. Tea alone reaches "
        "this list on price: its tonnage is a small fraction of wheat's or rice's.</div>"
        "<div class='cap-secondary'>EU countries are undercounted here since 2018. See Notes below "
        "for the per-crop reporting gap this chart shares with Crop Value vs. Cultivated Area.</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Two-up row: Crop bubble scatter | Production per Arable Hectare
# (this replaces the original's "Countries Sampled" text-wall slot)
# ---------------------------------------------------------------------------
col3, col4 = st.columns([1, 1])
ROW_HEIGHT = 555

with col3:
    with st.container(border=True, height=ROW_HEIGHT):
        st.markdown(f"<div class='panel-title'>Crop Value vs. Cultivated Area, {year}</div>", unsafe_allow_html=True)
        bubble = (
            year_crop_value_all.groupby("DisplayCrop", as_index=False)
            .agg(Value_kUSD=("Value_kUSD", "sum"),
                 AreaHarvested_ha=("AreaHarvested_ha", "sum"),
                 Countries=("Country", "nunique"))
        )
        bubble["Value_USD"] = bubble["Value_kUSD"] * 1000
        bubble = bubble.sort_values("Value_USD", ascending=False).head(15)
        label_set = set(bubble.sort_values("Value_USD", ascending=False).head(4)["DisplayCrop"])
        # Hollow/outlined bubbles (transparent fill, colored stroke) to match
        # the original's circle-outline style, rather than px.scatter's fill.
        size_max = 42
        sizeref = 2. * bubble["Countries"].max() / (size_max ** 2)
        fig = go.Figure(go.Scatter(
            x=bubble["AreaHarvested_ha"], y=bubble["Value_USD"],
            mode="markers", text=bubble["DisplayCrop"],
            marker=dict(
                size=bubble["Countries"], sizemode="area", sizeref=sizeref, sizemin=5,
                color="rgba(0,0,0,0)",
                line=dict(color=bubble["Value_USD"], colorscale=LINE_COLORS, width=4),
            ),
            hovertemplate="<b>%{text}</b><br>Cultivated Area: %{x:,.0f} ha<br>"
                          "Value: $%{y:,.0f}<extra></extra>",
        ))
        max_countries = bubble["Countries"].max()
        for _, row in bubble[bubble["DisplayCrop"].isin(label_set)].iterrows():
            # Push the label clear of the bubble's own edge - bigger bubbles
            # need a bigger offset, or the text cuts across the circle.
            offset = 14 + 14 * (row["Countries"] / max_countries) ** 0.5
            fig.add_annotation(x=row["AreaHarvested_ha"], y=row["Value_USD"], text=row["DisplayCrop"],
                                showarrow=False, yshift=offset, font=dict(color=TEXT, size=11))
        fig.update_layout(
            plot_bgcolor=SURFACE, paper_bgcolor=SURFACE, font_color=TEXT,
            margin=dict(l=10, r=10, t=10, b=10), height=400,
            xaxis_title="Cultivated Area (ha)", yaxis_title="Value (USD)",
        )
        fig.update_xaxes(gridcolor=GRID, fixedrange=True)
        fig.update_yaxes(gridcolor=GRID, fixedrange=True)
        st.plotly_chart(fig, width='stretch', config=dict(displayModeBar=False, scrollZoom=False, doubleClick=False))
        st.markdown(
            "<div class='cap-primary'>Each bubble is one crop. It plots total value against "
            "total cultivated area, across all reporting countries.</div>"
            "<div class='cap-secondary'>Bubble size shows the number of countries growing that "
            "crop with item-level value data.</div>"
            "<div class='cap-secondary'>This chart shares the same EU per-crop gap as Top Crops "
            "by Value above. See Notes below.</div>",
            unsafe_allow_html=True,
        )

with col4:
    with st.container(border=True, height=ROW_HEIGHT):
        st.markdown(f"<div class='panel-title'>Value per Arable Hectare {year}</div>", unsafe_allow_html=True)
        value_ha_year = (
            value_kcal[(value_kcal["Year"] == year) & (value_kcal["ArableLand_ha"] >= MIN_ARABLE_LAND_HA)]
            .sort_values("ValuePerArableHa_USD", ascending=False).head(TOP_N_BARS)
            .sort_values("ValuePerArableHa_USD")
        )
        prod_colors = (
            px.colors.sample_colorscale(LINE_COLORS, [i / (len(value_ha_year) - 1) for i in range(len(value_ha_year))])
            if len(value_ha_year) > 1 else LINE_COLORS[:1]
        )
        fig = go.Figure(go.Bar(
            x=value_ha_year["ValuePerArableHa_USD"], y=value_ha_year["DisplayCountry"], orientation="h",
            marker=dict(color=prod_colors),
        ))
        fig.update_layout(
            plot_bgcolor=SURFACE, paper_bgcolor=SURFACE, font_color=TEXT,
            margin=dict(l=10, r=10, t=10, b=10), height=400,
            xaxis_title="Value / arable ha (USD)",
        )
        fig.update_xaxes(gridcolor=GRID, fixedrange=True)
        fig.update_yaxes(gridcolor=GRID, fixedrange=True)
        st.plotly_chart(fig, width='stretch', config=dict(displayModeBar=False, scrollZoom=False, doubleClick=False))
        st.markdown(
            f"<div class='cap-primary'>This chart ranks the top {TOP_N_BARS} countries by value per "
            "arable hectare.</div>"
            "<div class='cap-secondary'>Arable land excludes permanent-crop land such as palm oil, "
            "bananas, and coffee. This favors tree-crop economies.</div>"
            f"<div class='cap-secondary'>This chart excludes countries with under {MIN_ARABLE_LAND_HA:,} ha "
            "of arable land. See Top Value above for why.</div>",
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Value vs Kcal per Arable Hectare (full width) - a sidenote panel, not part
# of the original layout: is a country's farmland ROI-oriented (an export/
# value business, teal->red skews red) or kcal-oriented (calorie production
# to feed people, skews teal)? Styled the same way as the Global Yield
# choropleth above (log-scale color, custom HTML gradient footnote instead of
# Plotly's own colorbar) but on the new DIVERGING_RAMP rather than SEQ_RAMP,
# since this is a contrast between two quantities, not a single ranked one.
# ---------------------------------------------------------------------------
vk_year = value_kcal[(value_kcal["Year"] == year)].dropna(subset=["ISO3", "ValuePerArableHa_USD", "KcalPerArableHa", "ArableLand_ha"]).copy()
# Drop country/years where reported crop-harvested area is a tiny sliver of
# the nominal arable-land figure both axes divide by (see
# MIN_CROP_LAND_UTILIZATION above) - otherwise the panel's log-z-score
# contrast reads that mismatch, not real economic behavior, as its most
# extreme data point.
utilization = (
    crops[crops["Year"] == year].dropna(subset=["AreaHarvested_ha"])
    .groupby("Country", as_index=False)["AreaHarvested_ha"].sum()
)
vk_year = vk_year.merge(utilization, on="Country", how="left")
vk_year = vk_year[
    (vk_year["AreaHarvested_ha"].fillna(0) / vk_year["ArableLand_ha"]) >= MIN_CROP_LAND_UTILIZATION
]
vk_year["log_value"] = np.log10(vk_year["ValuePerArableHa_USD"])
vk_year["log_kcal"] = np.log10(vk_year["KcalPerArableHa"])
_log_value_mean, _log_value_std = vk_year["log_value"].mean(), vk_year["log_value"].std()
_log_kcal_mean, _log_kcal_std = vk_year["log_kcal"].mean(), vk_year["log_kcal"].std()
vk_year["z_value"] = (vk_year["log_value"] - _log_value_mean) / _log_value_std
vk_year["z_kcal"] = (vk_year["log_kcal"] - _log_kcal_mean) / _log_kcal_std
vk_year["Contrast"] = vk_year["z_value"] - vk_year["z_kcal"]

# Estimated countries (see derive_value_gap_fill.py) get the same Contrast
# treatment, standardized against the REAL data's own mean/std above (not
# their own separate distribution) so they land on the same scale rather
# than a self-referential one. Same utilization floor as the real data.
est_vk_year = est_fill[est_fill["Year"] == year].dropna(
    subset=["ISO3", "ValuePerArableHa_USD_Est", "KcalPerArableHa_Est", "ArableLand_ha"]
).copy()
est_vk_year = est_vk_year.merge(utilization, on="Country", how="left")
est_vk_year = est_vk_year[
    (est_vk_year["AreaHarvested_ha"].fillna(0) / est_vk_year["ArableLand_ha"]) >= MIN_CROP_LAND_UTILIZATION
]
if not est_vk_year.empty:
    est_vk_year["z_value"] = (np.log10(est_vk_year["ValuePerArableHa_USD_Est"]) - _log_value_mean) / _log_value_std
    est_vk_year["z_kcal"] = (np.log10(est_vk_year["KcalPerArableHa_Est"]) - _log_kcal_mean) / _log_kcal_std
    est_vk_year["Contrast"] = est_vk_year["z_value"] - est_vk_year["z_kcal"]

# Bordered container matching every other panel - previously the only
# unframed chart on the page besides the (now also framed) header.
with st.container(border=True):
    st.markdown(
        f"<div class='panel-title'>$ vs kcal per Arable Hectare {year}</div>",
        unsafe_allow_html=True,
    )
    all_contrast = pd.concat([vk_year["Contrast"], est_vk_year["Contrast"]]) if not est_vk_year.empty else vk_year["Contrast"]
    contrast_bound = float(np.abs(all_contrast).max()) if len(all_contrast) else 1.0
    fig = px.choropleth(
        vk_year, locations="ISO3", color="Contrast", hover_name="DisplayCountry",
        color_continuous_scale=DIVERGING_RAMP, range_color=(-contrast_bound, contrast_bound),
        custom_data=["ValuePerArableHa_USD", "KcalPerArableHa"],
    )
    fig.update_traces(
        hovertemplate="<b>%{hovertext}</b><br>Value: $%{customdata[0]:,.0f}/ha<br>"
                      "Food energy: %{customdata[1]:,.1s}kcal/ha<extra></extra>",
        marker_line_width=0,
    )
    # Estimated countries - same uniform, borderless treatment as the Global
    # Value map, disclosed via hover only.
    if not est_vk_year.empty:
        fig.add_trace(go.Choropleth(
            locations=est_vk_year["ISO3"], z=est_vk_year["Contrast"],
            zmin=-contrast_bound, zmax=contrast_bound, coloraxis="coloraxis",
            customdata=est_vk_year[["ValuePerArableHa_USD_Est", "KcalPerArableHa_Est", "Confidence"]],
            hovertemplate="Estimated: $%{customdata[0]:,.0f}/ha<br>"
                          "Food energy: %{customdata[1]:,.1s}kcal/ha"
                          "<br><i>World Bank/FAOSTAT proxy (%{customdata[2]})</i><extra></extra>",
            marker_line_width=0,
        ))
    fig.update_layout(
        # Same geo crop as the Global Value map above, and revised the same
        # way (2026-07-29) - see that panel's comment for the full
        # measure-adjust-remeasure process behind these exact numbers.
        geo=dict(
            bgcolor="white", lakecolor="white", landcolor="#dcdcdc", showframe=False,
            showland=True, showcountries=False, showcoastlines=False,
            lataxis=dict(range=[-58, 78]), lonaxis=dict(range=[-160, 160]),
            projection=dict(scale=0.98, rotation=dict(lon=0)), center=dict(lon=0, lat=11),
        ),
        paper_bgcolor="white", font_color="#333333", autosize=True,
        # height=630, matching the Global Value map above, not an arbitrary
        # smaller value (500 previously) - Plotly's geo subplot letterboxes to
        # fit the container's own aspect ratio rather than the lataxis/lonaxis
        # crop (see the comment on the Global Value map), so a height that
        # doesn't match this same lon/lat range's natural ratio at full
        # container width leaves visible white bars down both sides even
        # though margin is already zero. Confirmed directly: at height=500 the
        # rendered landmass left off ~96px of white on each side of a 1408px-
        # wide container; at height=630 (matching this same width) it fills
        # edge to edge.
        margin=dict(l=0, r=0, t=0, b=0), height=630,
        dragmode=False,
        coloraxis_showscale=False,
    )
    st.plotly_chart(fig, width='stretch', config=dict(displayModeBar=False, scrollZoom=False, doubleClick=False))

    # Edge labels are anchored to the container's own left/right edge (growing
    # inward, not centered on a point) so long text can't bleed past the 320px
    # bar the way a centered/translateX(-50%) label would - only the short
    # middle label is centered on its point.
    vk_ticks_html = (
        "<span style='position:absolute; left:0; white-space:nowrap;'>kcal-oriented</span>"
        "<span style='position:absolute; left:50%; transform:translateX(-50%); white-space:nowrap;'>balanced</span>"
        "<span style='position:absolute; right:0; white-space:nowrap;'>ROI-oriented</span>"
    )
    est_vk_swatch = (
        f"""<div style='margin-top:8px;'>
            {est_vk_year["DisplayCountry"].nunique()} of the countries shown are estimated
            (World Bank/FAOSTAT proxy). Hover a country to see which.
        </div>"""
        if not est_vk_year.empty else ""
    )
    st.markdown(
        f"""
        <div style='margin-top:8px; font-size:12px; color:{MUTED};'>
            Teal &rarr; kcal-oriented &nbsp;|&nbsp; Red &rarr; ROI-oriented
            <div style='width:520px; height:12px; margin-top:4px; border-radius:2px;
                         background:linear-gradient(to right, {", ".join(DIVERGING_RAMP)});'></div>
            <div style='position:relative; width:520px; height:14px; margin-top:2px; font-size:11px;'>
                {vk_ticks_html}
            </div>
            {est_vk_swatch}
        </div>
        """,
        unsafe_allow_html=True,
    )
    _kcal_excluded_display = ", ".join(sorted(
        simplify_crop_name(c).lower()
        for c in KCAL_EXCLUDE_ITEMS - {"Other sugar crops n.e.c.", "Seed cotton; unginned"}
    ))
    st.markdown(
        "<div class='cap-primary'>Teal countries lean toward feeding people. Red countries lean "
        "toward export or cash value.</div>"
        "<div class='cap-secondary'>Both sides come from FAOSTAT's own figures. Value comes from "
        "Gross Production Value; food energy comes from Food Balance Sheets. Value data covers "
        "about 64% of tracked items and excludes coffee, onions, and most tree nuts. For the EU "
        "after 2017, this map uses FAOSTAT's aggregate categories instead of item-level data.</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"<div class='cap-secondary'>Food energy excludes crops that mostly become a separately-"
        f"tracked product: {_kcal_excluded_display}. These crops keep their full value on the "
        "dollar side, with nothing removed to match. Countries dominated by these crops (Malaysia, "
        "Mauritius, Barbados, Cabo Verde) skew toward red here. This is a data artifact, not "
        "always a real choice.</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"<div class='cap-secondary'>This map drops countries using under {MIN_CROP_LAND_UTILIZATION:.0%} "
        "of their nominal arable land for tracked crops. Below this level, a small numerator "
        "becomes an extreme data point. Iceland is the clearest case: only about 3% of its "
        "arable-land figure is actually cropped.</div>",
        unsafe_allow_html=True,
    )
    if not est_vk_year.empty:
        st.markdown(
            "<div class='cap-secondary'>Hover a country to see whether its figure is FAOSTAT-"
            "reported or estimated. For estimated countries, the food-energy side is real FAOSTAT "
            "data: their own reported crop production, run through the same kcal-per-tonne factors "
            "used elsewhere on this page. Only the value side is a World Bank estimate, the same "
            "as on the Global Value map above.</div>",
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Bottom row: Yield/Area by Country (left) | Conclusion + Notes (right)
# ---------------------------------------------------------------------------
col6, col5 = st.columns([2.2, 1])
BOTTOM_HEIGHT = 940

with col6:
    with st.container(border=True, height=BOTTOM_HEIGHT):
        # Title reserved here, filled in after the selector below it in the
        # script determines the country - so the title still renders first.
        title_slot = st.empty()
        all_countries = sorted(crops["DisplayCountry"].dropna().unique())
        default_country = "Netherlands" if "Netherlands" in all_countries else all_countries[0]
        focus_country = st.selectbox(
            "Country", all_countries, index=all_countries.index(default_country)
        )
        title_slot.markdown(f"<div class='panel-title'>Value &amp; Area — {focus_country}</div>", unsafe_allow_html=True)
        focus_row_country = crops.loc[crops["DisplayCountry"] == focus_country, "Country"].iloc[0]
        area_series = (
            crops[crops["Country"] == focus_row_country]
            .groupby("Year", as_index=False)
            .agg(AreaHarvested_ha=("AreaHarvested_ha", "sum"))
        )
        # Country-total value per arable ha (same value_kcal metric as the
        # Global Value map above), not a per-crop median - see the note above
        # that map for why: the median version of this line used to show the
        # Netherlands' (and other EU countries') value appearing to crash
        # >80% right at 2018, purely because item-level Value of Production
        # reporting stops for the EU's 27 member states that year and the
        # Eurostat fill-in only covers 12 field crops, not because farmland
        # value actually collapsed. value_kcal doesn't have that gap - it
        # keeps fruit/veg in via FAOSTAT's own aggregate categories - so this
        # line stays smooth across 2018 for EU countries. Real gaps (a
        # country/year value_kcal has no row for at all) still show as a
        # shorter line, same as any other missing data in this dashboard.
        value_series = (
            value_kcal[value_kcal["Country"] == focus_row_country][["Year", "ValuePerArableHa_USD"]]
            .rename(columns={"ValuePerArableHa_USD": "Value_per_ha"})
            .sort_values("Year")
        )
        # One combined dual-axis panel (Area + Value sharing the same plot
        # area, like the original) instead of two stacked subplots. Three
        # distinct palette tones: Area is a lime-green line (LINE_COLORS[0]),
        # Value's X-markers are flat solid teal (LINE_COLORS[2], no marker
        # outline), and the thin connecting line is a third, more cerulean
        # tone (SEQ_RAMP's blue end) so the two lines read as visually
        # distinct even though the markers and area line are both greenish.
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=area_series["Year"], y=area_series["AreaHarvested_ha"],
                                  mode="lines", line=dict(color=LINE_COLORS[0], width=5, shape="spline"),
                                  name="Area (ha)", yaxis="y1"))
        fig.add_trace(go.Scatter(x=value_series["Year"], y=value_series["Value_per_ha"],
                                  mode="lines+markers",
                                  marker=dict(symbol="x", size=24, color=LINE_COLORS[2]),
                                  line=dict(color="rgba(65,183,196,0.45)", width=2, shape="spline"),
                                  name="Value ($/arable ha)", yaxis="y2"))
        fig.update_layout(
            plot_bgcolor=SURFACE, paper_bgcolor=SURFACE, font_color=TEXT,
            margin=dict(l=10, r=10, t=10, b=10), height=700,
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            xaxis=dict(gridcolor=GRID, fixedrange=True),
            yaxis=dict(title="Area (ha)", gridcolor=GRID, fixedrange=True),
            yaxis2=dict(title="Value ($/arable ha)", overlaying="y", side="right", showgrid=False, fixedrange=True),
        )
        st.plotly_chart(fig, width='stretch', config=dict(displayModeBar=False, scrollZoom=False, doubleClick=False))
        if value_series.empty:
            st.markdown(
                f"<div class='cap-primary'>No value data available for {focus_country} in any year.</div>",
                unsafe_allow_html=True,
            )
        elif value_series["Year"].max() < area_series["Year"].max():
            st.markdown(
                f"<div class='cap-primary'>No value data for {focus_country} after "
                f"{int(value_series['Year'].max())}.</div>",
                unsafe_allow_html=True,
            )

with col5:
    with st.container(border=True, height=BOTTOM_HEIGHT):
        st.markdown("<div class='panel-title'>Conclusion &amp; Notes</div>", unsafe_allow_html=True)
        st.markdown(
            f"""
            <div class="callout-box">
            <b>Top Yield Countries</b> <span style="color:{MUTED}; font-size:11.5px;">(fixed cohort)</span><br>
            2012: {top_2012:.1f} t/ha &rarr; 2022: {top_2022:.1f} t/ha ({top_pct:+.1f}%)<br>
            <br>
            <b>Global Median</b><br>
            2012: {glob_2012:.1f} t/ha &rarr; 2022: {glob_2022:.1f} t/ha ({glob_pct:+.1f}%)
            <span class="insight">Top producers are pulling further ahead of the global median.
            This is a fixed historical benchmark. It does not depend on which country currently
            tops the Top Value chart.</span>
            <span class="notes">
            This benchmark stays in tonnage. Unlike most of this page, it does not use dollars.
            The cohort is defined by tonnage yield, so scoring it in dollars would answer a
            different question. The Top Value panel above already covers that question. The
            Global Median side is checked against all 178 countries in this dashboard. About 18%
            of these have never reported Value of Production to FAOSTAT. Switching this
            comparator to dollars would shrink it by the same share.<br><br>
            This chart uses crops only, not livestock or rollup categories. Yield equals
            production divided by harvested area. Cross-crop figures use the median, which is
            more robust to outlier crops such as greenhouse produce. This chart excludes
            territories under {MIN_LAND_AREA_KM2:,} km&sup2; and country-years reporting fewer
            than {MIN_REPORTED_CROPS} crops.<br><br>
            Top Crops by Value and Crop Value vs. Cultivated Area, above, undercount the EU after
            2017. Eurostat fills in cereals, oilseeds, sugar beet, and tobacco. Fruit, vegetables,
            wine, and olives are not recoverable at that per-crop level.<br><br>
            The $ vs kcal map's color is relative to that year's own set of countries, not a fixed
            scale. Moving the year slider changes which countries a given country is compared
            against. A country's color can shift between years even when its own value and food
            energy stay flat, because the comparison group shifted, not the country.<br><br>
            The Top Yield Countries cohort's nine members come from the original Tableau
            workbook's own selection. That original criterion is not documented here and cannot
            be reproduced. The cohort stays fixed going forward for a consistent benchmark, not
            because these nine are provably the best nine to track.<br><br>
            Data quality varies by country. FAOSTAT flags each figure as official, estimated, or
            imputed, but this dashboard does not show that flag. Value per Arable Hectare compares
            each year's value against one recent arable-land figure, not that year's own figure.
            For example, 2005 production is divided by present-day land area. Arable land changes
            slowly, so this effect is small.<br><br>
            Source: FAOSTAT (QCL, QV domains) and World Bank (AG.LND.ARBL.HA). Both may revise
            figures in later releases.
            </span>
            </div>
            """,
            unsafe_allow_html=True,
        )
