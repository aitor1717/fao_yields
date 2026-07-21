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
~65% of tracked crop items, and the EU's 27 member states stop reporting
item-level detail entirely after 2017 - confirmed directly, all 27 report
through 2017 and precisely zero of them do from 2018 onward, a clean cutoff
pointing to a reporting-format change on the EU's side, not a gradual
data-quality drift.

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
deliberately: it depends on the same 9 countries reporting every year from
2012-2022, and 5 of them (including the Netherlands, an EU member) have no
2022 value data at all - switching that specific benchmark to dollars would
silently shrink its own cohort mid-comparison. See derive_value_kcal.py and
each panel's own caption for the full rationale.

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
# Every other country checked clusters near full utilization (median 0.95
# across all 190 countries in 2022) - only Iceland is a genuine outlier
# (0.03: 3,528 ha actually cropped against a nominal 121,000 ha "arable"
# figure, the rest presumably hay/pasture land FAOSTAT's crop domain doesn't
# track as an item). At that ratio, both of the panel's per-arable-ha figures
# are dividing a real, small numerator by a denominator that mostly isn't
# describing the same land, and the panel's log-z-score contrast (see below)
# reads that mismatch as the single most extreme "runs as a business" data
# point on the map - confirmed directly, Iceland topped that ranking by a
# wide margin before this filter. A general ratio threshold, not a
# hardcoded per-country exclusion, so it applies to whichever country the
# data next produces this pattern for, not just Iceland today.
MIN_CROP_LAND_UTILIZATION = 0.05

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
    crops = crops[~crops["Country"].isin(small_countries)]
    value_kcal = value_kcal[~value_kcal["Country"].isin(small_countries)]
    crop_value = crop_value[~crop_value["Country"].isin(small_countries)]

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
    # ~65% of items, and drops to country-total-only for the EU's 27 member
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
    return crops, value_kcal, crop_value


crops, value_kcal, crop_value = load_data()

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
    /* Subtle hint that the $-vs-kcal panel is a sidenote, not a core figure -
       a soft tint on just the word "kcal" in its own title/legend, not a
       loud callout. */
    .sidenote-accent {{ color: {RED_ACCENT}; opacity: 0.75; }}
    .callout-box b {{ color: #ffffff; }}
    .callout-box .insight {{ color: {MUTED}; font-size: 12.5px; margin-top: 10px; display:block; }}
    .callout-box .notes {{ color: {MUTED}; font-size: 12px; line-height: 1.6; margin-top: 4px; display:block; }}
    div[data-testid="stMetric"] {{ background-color: transparent; padding: 0; }}
    div[data-testid="stSelectbox"] div[role="group"] {{
        background-color: transparent !important; border: 1px solid {GRID} !important;
        border-radius: 4px;
    }}
    div[data-testid="stSelectbox"] input {{ background-color: transparent !important; }}
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

st.markdown(
    f"<div style='font-size:28px; font-weight:700; color:{TEXT}; margin-bottom:0;'>Global Crop Yields</div>",
    unsafe_allow_html=True,
)
st.caption(
    "What does each country produce with the land it has? FAO/World Bank data, "
    f"{int(crops['Year'].min())}–{int(crops['Year'].max())}. A fixed cohort of historically "
    f"top-yielding countries has pulled further ahead of the global median since 2012 "
    f"({top_pct:+.1f}% vs {glob_pct:+.1f}%)."
)

# ---------------------------------------------------------------------------
# Year control - a slider (matching the original workbook) right under the
# title, since it's the one control that's genuinely global (every panel
# reads it). Years are a contiguous range (2005-2022, confirmed directly),
# so a plain slider works without needing st.select_slider's explicit option list.
# ---------------------------------------------------------------------------
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
tick_vals = [500, 1000, 2500, 5000, 10000, 20000]
fig = px.choropleth(
    map_df, locations="ISO3", color="ColorValue", hover_name="DisplayCountry",
    color_continuous_scale=SEQ_RAMP, custom_data=["ValuePerArableHa_USD"],
)
fig.update_traces(
    hovertemplate="<b>%{hovertext}</b><br>Value: $%{customdata[0]:,.0f}/arable ha<extra></extra>",
    marker_line_width=0,  # the choropleth trace draws its own polygon borders,
)  # separate from geo.showcountries - both needed off to fully remove lines
fig.update_layout(
    # White basemap (matching the original) rather than dark-on-dark. No
    # country borders or coastline outlines - countries are told apart by
    # color contrast alone. Light gray (not white) for countries with no
    # yield data, distinct from the white ocean and the colored countries;
    # showland must be explicit or the land layer doesn't render at all.
    # lataxis/lonaxis crop tightly to the populated landmass extent - this is
    # deliberately manual rather than fitbounds="locations", which pulled in
    # a disconnected, badly-distorted sliver of Antarctica at the edge. The
    # lonaxis seam is at -176/178, not the antimeridian (-180/180) - that
    # exact split runs through Alaska's mainland/Aleutian chain and clips it.
    # projection.scale/center trim the excess ocean margin left of Alaska and
    # right of Russia that a plain lataxis/lonaxis range still leaves (Plotly
    # letterboxes to the geo subplot's own aspect ratio, not tightly to the
    # range) - confirmed empirically: scale alone re-centers on its own and
    # clips Alaska, so center must be pinned to the range's own midpoint
    # (lon=1, matching (-176+178)/2) for the zoom to stay symmetric.
    geo=dict(
        bgcolor="white", lakecolor="white", landcolor="#dcdcdc", showframe=False,
        showland=True, showcountries=False, showcoastlines=False,
        lataxis=dict(range=[-56, 78]), lonaxis=dict(range=[-176, 178]),
        projection=dict(scale=1.13, rotation=dict(lon=1)), center=dict(lon=1, lat=11),
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
cmin, cmax = map_df["ColorValue"].min(), map_df["ColorValue"].max()
tick_labels = {v: f"${v // 1000}K" if v >= 1000 else f"${v}" for v in tick_vals}
ticks_html = "".join(
    f"<span style='position:absolute; left:{(np.log10(v) - cmin) / (cmax - cmin) * 100:.1f}%; "
    f"transform:translateX(-50%);'>{tick_labels[v]}</span>"
    for v in tick_vals if cmin <= np.log10(v) <= cmax
)
st.markdown(
    f"""
    <div style='margin-top:8px; font-size:12px; color:{MUTED};'>
        Value ($/arable ha)
        <div style='width:320px; height:12px; margin-top:4px; border-radius:2px;
                     background:linear-gradient(to right, {", ".join(SEQ_RAMP)});'></div>
        <div style='position:relative; width:320px; height:14px; margin-top:2px; font-size:11px;'>
            {ticks_html}
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)
st.caption("Log color scale (value is right-skewed) — see hover for exact values.")
st.caption(
    "Dollars fix the unit problem tonnage had here — a ton of tomatoes and a ton of "
    "wheat are genuinely different things, but a dollar of either is comparable. This "
    "is each country's total crop production value divided by its arable land, not a "
    "per-crop figure, so it isn't skewed by which individual crops a country happens "
    "to report in a given year (see 'Value per Arable Hectare' below for the same "
    "metric ranked)."
)
st.caption(
    "All 27 EU member states stopped reporting item-level Value of Production to "
    "FAOSTAT after 2017, but this map uses each country's total, which FAOSTAT still "
    "reports via its own aggregate categories (Cereals primary, Vegetables and Fruit "
    "Primary, Roots and Tubers Total, Sugar Crops Primary) — so EU countries stay "
    "smooth and comparable across the 2017/2018 boundary here. That item-level gap "
    "does still affect the per-crop panels further down this page (Top Crops by "
    "Value, Crop Value vs. Cultivated Area) — Eurostat fills in cereals, oilseeds, "
    "sugar beet, and tobacco for those where possible, but fruit, vegetables, wine, "
    "and olives remain unrecoverable at the per-crop level for the EU after 2017."
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
    top_countries_now = (
        value_kcal[value_kcal["Year"] == latest_year]
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
    st.caption(
        f"Every current leader ({', '.join(top_countries_now)}) runs intensive, "
        "high-value production (often greenhouse horticulture or export crops) on a "
        "small arable-land base, not just high yields spread over lots of land."
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
    st.caption(
        f"Top {TOP_N_BARS} by value, summed across countries reporting item-level data — "
        "not quite the same ranking as by tonnage (Tea leaves cracks this list on price "
        "alone; global tea tonnage is a fraction of wheat's or rice's)."
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
            margin=dict(l=10, r=10, t=10, b=10), height=380,
            xaxis_title="Cultivated Area (ha)", yaxis_title="Value (USD)",
        )
        fig.update_xaxes(gridcolor=GRID, fixedrange=True)
        fig.update_yaxes(gridcolor=GRID, fixedrange=True)
        st.plotly_chart(fig, width='stretch', config=dict(displayModeBar=False, scrollZoom=False, doubleClick=False))
        st.caption("Bubble size: number of countries growing that crop (with item-level value data).")

with col4:
    with st.container(border=True, height=ROW_HEIGHT):
        st.markdown(f"<div class='panel-title'>Value per Arable Hectare {year}</div>", unsafe_allow_html=True)
        value_ha_year = (
            value_kcal[value_kcal["Year"] == year]
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
            margin=dict(l=10, r=10, t=10, b=10), height=380,
            xaxis_title="Value / arable ha (USD)",
        )
        fig.update_xaxes(gridcolor=GRID, fixedrange=True)
        fig.update_yaxes(gridcolor=GRID, fixedrange=True)
        st.plotly_chart(fig, width='stretch', config=dict(displayModeBar=False, scrollZoom=False, doubleClick=False))
        st.caption(
            f"Top {TOP_N_BARS} by value per arable hectare. Arable land excludes "
            "permanent-crop land (palm oil, bananas, coffee) — favors tree-crop economies."
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
vk_year["z_value"] = (vk_year["log_value"] - vk_year["log_value"].mean()) / vk_year["log_value"].std()
vk_year["z_kcal"] = (vk_year["log_kcal"] - vk_year["log_kcal"].mean()) / vk_year["log_kcal"].std()
vk_year["Contrast"] = vk_year["z_value"] - vk_year["z_kcal"]

st.markdown(
    f"<div class='panel-title'>$ vs <span class='sidenote-accent'>kcal</span> per Arable Hectare {year}</div>",
    unsafe_allow_html=True,
)
contrast_bound = float(np.abs(vk_year["Contrast"]).max()) if len(vk_year) else 1.0
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
fig.update_layout(
    geo=dict(
        bgcolor="white", lakecolor="white", landcolor="#dcdcdc", showframe=False,
        showland=True, showcountries=False, showcoastlines=False,
        lataxis=dict(range=[-56, 78]), lonaxis=dict(range=[-176, 178]),
        projection=dict(scale=1.13, rotation=dict(lon=1)), center=dict(lon=1, lat=11),
    ),
    paper_bgcolor="white", font_color="#333333", autosize=True,
    margin=dict(l=0, r=0, t=0, b=0), height=500,
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
st.markdown(
    f"""
    <div style='margin-top:8px; font-size:12px; color:{MUTED};'>
        Teal &rarr; <span class="sidenote-accent">kcal</span>-oriented &nbsp;|&nbsp; Red &rarr; ROI-oriented
        <div style='width:320px; height:12px; margin-top:4px; border-radius:2px;
                     background:linear-gradient(to right, {", ".join(DIVERGING_RAMP)});'></div>
        <div style='position:relative; width:320px; height:14px; margin-top:2px; font-size:11px;'>
            {vk_ticks_html}
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)
_kcal_excluded_display = ", ".join(sorted(
    simplify_crop_name(c).lower()
    for c in KCAL_EXCLUDE_ITEMS - {"Other sugar crops n.e.c.", "Seed cotton; unginned"}
))
st.caption(
    "Standardized log contrast of value and food energy per arable hectare (both sides use "
    "FAOSTAT's own figures: Gross Production Value, constant 2014-2016 USD, and food energy "
    "factors derived from Food Balance Sheets). Value covers ~65% of tracked crop items (no "
    "coffee, onions, most tree nuts) and switches to FAOSTAT's own aggregate categories for the "
    "EU's 27 member states, which stopped reporting item-level detail after 2017."
)
st.caption(
    f"Food energy covers ~63% of tonnage — {_kcal_excluded_display} are excluded because most "
    "of each harvest becomes a separately-tracked product (palm oil, sugar, cooking oil, beer) "
    "this calculation can't trace through. These same crops keep their full value on the $ side, "
    "with nothing removed to match — so any country whose leading crop is one of these is "
    "mechanically pulled toward ROI-oriented here, independent of how it actually runs its "
    "farmland. Confirmed directly: Malaysia (oil palm fruit, ~70% of its 2022 harvested area), "
    "and Mauritius, Barbados, and Cabo Verde (all sugar cane-dominated) land at the ROI-heavy "
    "extreme largely for this reason, not a genuine value-over-calories choice. Read a country's "
    "position here as partly reflecting this gap, not purely its real food-vs-cash-crop balance."
)
st.caption(
    f"Countries reporting crop area under {MIN_CROP_LAND_UTILIZATION:.0%} of their World Bank "
    "arable-land figure are dropped from this panel — both axes divide by that figure, and a "
    "country using almost none of it for tracked crops turns a small, not-very-meaningful "
    "numerator into an artificially extreme data point (Iceland: 3% of its arable land is "
    "actually cropped, the rest presumably hay or pasture FAOSTAT's crop domain doesn't track)."
)


# ---------------------------------------------------------------------------
# Bottom row: Yield/Area by Country (left) | Conclusion + Notes (right)
# ---------------------------------------------------------------------------
col6, col5 = st.columns([2.2, 1])
BOTTOM_HEIGHT = 660

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
            margin=dict(l=10, r=10, t=10, b=10), height=380,
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            xaxis=dict(gridcolor=GRID, fixedrange=True),
            yaxis=dict(title="Area (ha)", gridcolor=GRID, fixedrange=True),
            yaxis2=dict(title="Value ($/arable ha)", overlaying="y", side="right", showgrid=False, fixedrange=True),
        )
        st.plotly_chart(fig, width='stretch', config=dict(displayModeBar=False, scrollZoom=False, doubleClick=False))
        if value_series.empty:
            st.caption(f"No value data available for {focus_country} in any year.")
        elif value_series["Year"].max() < area_series["Year"].max():
            st.caption(
                f"No value data for {focus_country} after {int(value_series['Year'].max())}."
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
            This is a fixed historical benchmark, independent of who currently tops the Top
            Value chart.</span>
            <span class="notes">
            This benchmark stays in tonnage deliberately, unlike most of the rest of this page:
            it needs the same 9 countries reporting every single year 2012-2022, and 5 of them
            (including the Netherlands) have no item-level value data at all in 2022 &mdash;
            switching it to dollars would silently shrink its own cohort mid-comparison.<br><br>
            Crops only (no livestock/rollups). Yield = production &divide; harvested area.
            Cross-crop figures use the median, robust to outlier crops like greenhouse produce.
            Excludes territories under {MIN_LAND_AREA_KM2:,} km&sup2; and country-years reporting
            fewer than {MIN_REPORTED_CROPS} crops.<br><br>
            Data quality varies by country: FAOSTAT flags each figure as official, estimated, or
            imputed, but that distinction isn't carried through here. Value per Arable Hectare
            compares each year's value against a single, recent arable-land snapshot rather than
            that year's actual figure &mdash; e.g., 2005 production is divided by present-day
            land area, not 2005's. Arable land changes slowly, so the effect is minor.<br><br>
            Source: FAOSTAT (QCL, QV domains), World Bank (AG.LND.ARBL.HA) &mdash; all may revise
            figures in later releases.
            </span>
            </div>
            """,
            unsafe_allow_html=True,
        )
