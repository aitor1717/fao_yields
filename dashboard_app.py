"""
Streamlit version of the FAO Global Crop Yields dashboard, styled and arranged
to mimic the original Tableau layout: title top-left, Top Yields line (full
width, direct labels, no legend), Global Yield choropleth (full width), Top
Crops bar (full width, rank-graded color), a two-up row (crop bubble scatter +
the new Production-per-Arable-Hectare chart, in the old "Countries Sampled"
slot), and a bottom row pairing the Yield/Area chart with a notes/conclusion
callout.

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

from fao_filters import WB_TO_FAO_COUNTRY

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
    productivity = pd.read_csv("FAO_Arable_Land_Productivity.csv")

    land_area = pd.read_csv("area_data.csv").rename(columns={"country": "Country", "area": "LandArea_km2"})
    land_area["Country"] = land_area["Country"].replace(WB_TO_FAO_COUNTRY)
    small_countries = set(land_area.loc[land_area["LandArea_km2"] < MIN_LAND_AREA_KM2, "Country"])
    crops = crops[~crops["Country"].isin(small_countries)]
    productivity = productivity[~productivity["Country"].isin(small_countries)]

    # Per (Country, Year), require at least MIN_REPORTED_CROPS distinct crops
    # with actual yield data. Applied to every panel that reads Yield_tonha.
    reported = (
        crops.dropna(subset=["Yield_tonha"])
        .groupby(["Country", "Year"])["Crop"].nunique()
        .rename("ReportedCrops").reset_index()
    )
    crops = crops.merge(reported, on=["Country", "Year"], how="left")
    crops = crops[crops["ReportedCrops"].fillna(0) >= MIN_REPORTED_CROPS].drop(columns="ReportedCrops")

    crops["DisplayCountry"] = crops["Country"].replace(DISPLAY_NAME_OVERRIDES)
    crops["DisplayCrop"] = crops["Crop"].apply(simplify_crop_name)
    crops["ISO3"] = crops["Country"].apply(to_iso3)

    productivity["DisplayCountry"] = productivity["Country"].replace(DISPLAY_NAME_OVERRIDES)
    return crops, productivity


crops, productivity = load_data()

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
# Year control - a selectbox (like Country below) right under the title,
# since it's the one control that's genuinely global (every panel reads it).
# ---------------------------------------------------------------------------
years = sorted(crops["Year"].unique().tolist())
year = st.selectbox("Year", years, index=years.index(2022) if 2022 in years else len(years) - 1)
year_crops = crops[crops["Year"] == year]

# ---------------------------------------------------------------------------
# Global Yield choropleth (full width)
# ---------------------------------------------------------------------------
st.markdown(f"<div class='panel-title'>Global Yield {year}</div>", unsafe_allow_html=True)
map_df = (
    year_crops.dropna(subset=["ISO3", "Yield_tonha"])
    .groupby(["ISO3", "DisplayCountry"], as_index=False)["Yield_tonha"].median()
)
# Color on a log scale rather than the raw value: yield is heavily
# right-skewed, so a linear scale spends nearly all its range on gaps
# between the top few countries and leaves the rest of the map visually
# flat. Only the color *mapping* changes here - the underlying values
# (and the hover tooltip) are the real medians.
map_df["ColorValue"] = np.log10(map_df["Yield_tonha"])
tick_vals = [2, 5, 10, 20, 40, 80]
fig = px.choropleth(
    map_df, locations="ISO3", color="ColorValue", hover_name="DisplayCountry",
    color_continuous_scale=SEQ_RAMP, custom_data=["Yield_tonha"],
)
fig.update_traces(
    hovertemplate="<b>%{hovertext}</b><br>Median yield: %{customdata[0]:.1f} t/ha<extra></extra>",
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
    # lonaxis seam is at -170/180, not the antimeridian (-180/180) - that
    # exact split runs through Alaska's mainland/Aleutian chain and clips it.
    geo=dict(
        bgcolor="white", lakecolor="white", landcolor="#dcdcdc", showframe=False,
        showland=True, showcountries=False, showcoastlines=False,
        lataxis=dict(range=[-56, 78]), lonaxis=dict(range=[-170, 180]),
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
ticks_html = "".join(
    f"<span style='position:absolute; left:{(np.log10(v) - cmin) / (cmax - cmin) * 100:.1f}%; "
    f"transform:translateX(-50%);'>{v}</span>"
    for v in tick_vals if cmin <= np.log10(v) <= cmax
)
st.markdown(
    f"""
    <div style='margin-top:8px; font-size:12px; color:{MUTED};'>
        Median Yield (t/ha)
        <div style='width:320px; height:12px; margin-top:4px; border-radius:2px;
                     background:linear-gradient(to right, {", ".join(SEQ_RAMP)});'></div>
        <div style='position:relative; width:320px; height:14px; margin-top:2px; font-size:11px;'>
            {ticks_html}
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)
st.caption("Log color scale (yield is right-skewed) — see hover for exact values.")
st.caption(
    "This is the median across each country's own crop mix, not a like-for-like "
    "efficiency score — tons of tomatoes and tons of wheat aren't comparable. "
    "Countries growing almost nothing but produce (Guyana, Oman, Kuwait) outrank "
    "diversified producers like the Netherlands, whose individual greenhouse yields "
    "are the highest in this data but whose median is pulled down by its own grain "
    "crops. See Top Yields below."
)


# ---------------------------------------------------------------------------
# Top Yields (full width, direct end-of-line labels, no legend)
# ---------------------------------------------------------------------------
with st.container(border=True):
    st.markdown("<div class='panel-title'>Top Yields</div>", unsafe_allow_html=True)
    latest_year = crops["Year"].max()
    n_top = 6
    top_countries_now = (
        crops[crops["Year"] == latest_year]
        .groupby("DisplayCountry")["Yield_tonha"].median()
        .sort_values(ascending=False).head(n_top).index.tolist()
    )
    trend = crops[
        (crops["DisplayCountry"].isin(top_countries_now)) & (crops["Year"] >= 2012)
    ].groupby(["DisplayCountry", "Year"], as_index=False)["Yield_tonha"].median()

    # Color by rank along the same pale-yellow -> teal ramp the choropleth
    # uses, so "more teal" consistently means "higher value" across the
    # whole dashboard, rather than an arbitrary rotation through a palette.
    n = len(top_countries_now)
    rank_colors = px.colors.sample_colorscale(SEQ_RAMP, [i / (n - 1) for i in range(n)]) if n > 1 else SEQ_RAMP[-1:]

    value_range = trend["Yield_tonha"].max() - trend["Yield_tonha"].min()
    min_label_gap = value_range * 0.09
    label_y = sorted(
        (trend[trend["DisplayCountry"] == c].sort_values("Year")["Yield_tonha"].iloc[-1], c)
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
        color = rank_colors[n - 1 - i]  # rank 0 = highest yield -> darkest/teal-est
        fig.add_trace(go.Scatter(
            x=sub["Year"], y=sub["Yield_tonha"], mode="lines", name=c,
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
        yaxis_title="Median Yield (t / harvested ha)",
    )
    fig.update_xaxes(gridcolor=GRID, showgrid=False, fixedrange=True)
    fig.update_yaxes(showgrid=False, fixedrange=True)
    st.plotly_chart(fig, width='stretch', config=dict(displayModeBar=False, scrollZoom=False, doubleClick=False))
    st.caption(
        f"Every current leader ({', '.join(top_countries_now)}) is driven by intensive "
        "greenhouse/irrigated vegetables (tomatoes, cucumbers, peppers), not staple grains — "
        "this measures horticultural intensity, not overall farm productivity."
    )


# ---------------------------------------------------------------------------
# Top Crops bar (full width, rank-graded color to match the original)
# ---------------------------------------------------------------------------
with st.container(border=True):
    st.markdown(f"<div class='panel-title'>Top Crops by Production, {year}</div>", unsafe_allow_html=True)
    top_crops = (
        year_crops.groupby("DisplayCrop", as_index=False)["Production_tons"].sum()
        .sort_values("Production_tons", ascending=False).head(TOP_N_BARS)
    )
    top_crops = top_crops.sort_values("Production_tons")
    rank_colors = px.colors.sample_colorscale(LINE_COLORS, [i / (len(top_crops) - 1) for i in range(len(top_crops))]) if len(top_crops) > 1 else LINE_COLORS[:1]

    fig = go.Figure(go.Bar(
        x=top_crops["Production_tons"], y=top_crops["DisplayCrop"], orientation="h",
        marker=dict(color=rank_colors),
    ))
    fig.update_layout(
        plot_bgcolor=SURFACE, paper_bgcolor=SURFACE, font_color=TEXT,
        margin=dict(l=10, r=10, t=10, b=10), height=340,
        xaxis_title="Production (t)",
    )
    fig.update_xaxes(gridcolor=GRID, fixedrange=True)
    fig.update_yaxes(gridcolor=GRID, fixedrange=True)
    st.plotly_chart(fig, width='stretch', config=dict(displayModeBar=False, scrollZoom=False, doubleClick=False))
    st.caption(f"Top {TOP_N_BARS} by production.")


# ---------------------------------------------------------------------------
# Two-up row: Crop bubble scatter | Production per Arable Hectare
# (this replaces the original's "Countries Sampled" text-wall slot)
# ---------------------------------------------------------------------------
col3, col4 = st.columns([1, 1])
ROW_HEIGHT = 505

with col3:
    with st.container(border=True, height=ROW_HEIGHT):
        st.markdown(f"<div class='panel-title'>Crop Production vs. Cultivated Area, {year}</div>", unsafe_allow_html=True)
        bubble = (
            year_crops.groupby("DisplayCrop", as_index=False)
            .agg(Production_tons=("Production_tons", "sum"),
                 AreaHarvested_ha=("AreaHarvested_ha", "sum"),
                 Countries=("Country", "nunique"))
        )
        bubble = bubble.sort_values("Production_tons", ascending=False).head(15)
        label_set = set(bubble.sort_values("Production_tons", ascending=False).head(4)["DisplayCrop"])
        # Hollow/outlined bubbles (transparent fill, colored stroke) to match
        # the original's circle-outline style, rather than px.scatter's fill.
        size_max = 42
        sizeref = 2. * bubble["Countries"].max() / (size_max ** 2)
        fig = go.Figure(go.Scatter(
            x=bubble["AreaHarvested_ha"], y=bubble["Production_tons"],
            mode="markers", text=bubble["DisplayCrop"],
            marker=dict(
                size=bubble["Countries"], sizemode="area", sizeref=sizeref, sizemin=5,
                color="rgba(0,0,0,0)",
                line=dict(color=bubble["Production_tons"], colorscale=LINE_COLORS, width=4),
            ),
            hovertemplate="<b>%{text}</b><br>Cultivated Area: %{x:,.0f} ha<br>"
                          "Production: %{y:,.0f} t<extra></extra>",
        ))
        max_countries = bubble["Countries"].max()
        for _, row in bubble[bubble["DisplayCrop"].isin(label_set)].iterrows():
            # Push the label clear of the bubble's own edge - bigger bubbles
            # need a bigger offset, or the text cuts across the circle.
            offset = 14 + 14 * (row["Countries"] / max_countries) ** 0.5
            fig.add_annotation(x=row["AreaHarvested_ha"], y=row["Production_tons"], text=row["DisplayCrop"],
                                showarrow=False, yshift=offset, font=dict(color=TEXT, size=11))
        fig.update_layout(
            plot_bgcolor=SURFACE, paper_bgcolor=SURFACE, font_color=TEXT,
            margin=dict(l=10, r=10, t=10, b=10), height=380,
            xaxis_title="Cultivated Area (ha)", yaxis_title="Production (t)",
        )
        fig.update_xaxes(gridcolor=GRID, fixedrange=True)
        fig.update_yaxes(gridcolor=GRID, fixedrange=True)
        st.plotly_chart(fig, width='stretch', config=dict(displayModeBar=False, scrollZoom=False, doubleClick=False))
        st.caption("Bubble size: number of countries growing that crop.")

with col4:
    with st.container(border=True, height=ROW_HEIGHT):
        st.markdown(f"<div class='panel-title'>Production per Arable Hectare {year}</div>", unsafe_allow_html=True)
        prod_year = (
            productivity[productivity["Year"] == year]
            .sort_values("ProductionPerArableHa_tons", ascending=False).head(TOP_N_BARS)
            .sort_values("ProductionPerArableHa_tons")
        )
        prod_colors = (
            px.colors.sample_colorscale(LINE_COLORS, [i / (len(prod_year) - 1) for i in range(len(prod_year))])
            if len(prod_year) > 1 else LINE_COLORS[:1]
        )
        fig = go.Figure(go.Bar(
            x=prod_year["ProductionPerArableHa_tons"], y=prod_year["DisplayCountry"], orientation="h",
            marker=dict(color=prod_colors),
        ))
        fig.update_layout(
            plot_bgcolor=SURFACE, paper_bgcolor=SURFACE, font_color=TEXT,
            margin=dict(l=10, r=10, t=10, b=10), height=380,
            xaxis_title="Production / arable ha (t)",
        )
        fig.update_xaxes(gridcolor=GRID, fixedrange=True)
        fig.update_yaxes(gridcolor=GRID, fixedrange=True)
        st.plotly_chart(fig, width='stretch', config=dict(displayModeBar=False, scrollZoom=False, doubleClick=False))
        st.caption(
            f"Top {TOP_N_BARS} by production per arable hectare. Arable land excludes "
            "permanent-crop land (palm oil, bananas, coffee) — favors tree-crop economies."
        )


# ---------------------------------------------------------------------------
# Bottom row: Yield/Area by Country (left) | Conclusion + Notes (right)
# ---------------------------------------------------------------------------
col6, col5 = st.columns([2.2, 1])
BOTTOM_HEIGHT = 535

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
        title_slot.markdown(f"<div class='panel-title'>Yield &amp; Area — {focus_country}</div>", unsafe_allow_html=True)
        focus_row_country = crops.loc[crops["DisplayCountry"] == focus_country, "Country"].iloc[0]
        country_series = (
            crops[crops["Country"] == focus_row_country]
            .groupby("Year", as_index=False)
            .agg(AreaHarvested_ha=("AreaHarvested_ha", "sum"), Yield_tonha=("Yield_tonha", "median"))
        )
        # One combined dual-axis panel (Area + Yield sharing the same plot
        # area, like the original) instead of two stacked subplots. Three
        # distinct palette tones: Area is a lime-green line (LINE_COLORS[0]),
        # Yield's X-markers are flat solid teal (LINE_COLORS[2], no marker
        # outline), and the thin connecting line is a third, more cerulean
        # tone (SEQ_RAMP's blue end) so the two lines read as visually
        # distinct even though the markers and area line are both greenish.
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=country_series["Year"], y=country_series["AreaHarvested_ha"],
                                  mode="lines", line=dict(color=LINE_COLORS[0], width=5, shape="spline"),
                                  name="Area (ha)", yaxis="y1"))
        fig.add_trace(go.Scatter(x=country_series["Year"], y=country_series["Yield_tonha"],
                                  mode="lines+markers",
                                  marker=dict(symbol="x", size=24, color=LINE_COLORS[2]),
                                  line=dict(color="rgba(65,183,196,0.45)", width=2, shape="spline"),
                                  name="Median Yield", yaxis="y2"))
        fig.update_layout(
            plot_bgcolor=SURFACE, paper_bgcolor=SURFACE, font_color=TEXT,
            margin=dict(l=10, r=10, t=10, b=10), height=380,
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            xaxis=dict(gridcolor=GRID, fixedrange=True),
            yaxis=dict(title="Area (ha)", gridcolor=GRID, fixedrange=True),
            yaxis2=dict(title="Median Yield (t/ha)", overlaying="y", side="right", showgrid=False, fixedrange=True),
        )
        st.plotly_chart(fig, width='stretch', config=dict(displayModeBar=False, scrollZoom=False, doubleClick=False))

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
            Yields chart.</span>
            <span class="notes">
            Crops only (no livestock/rollups). Yield = production &divide; harvested area.
            Cross-crop figures use the median, robust to outlier crops like greenhouse produce.
            Excludes territories under {MIN_LAND_AREA_KM2:,} km&sup2; and country-years reporting
            fewer than {MIN_REPORTED_CROPS} crops.<br><br>
            Data quality varies by country: FAOSTAT flags each figure as official, estimated, or
            imputed, but that distinction isn't carried through here. Production per Arable
            Hectare compares each year's production against a single, recent arable-land snapshot
            rather than that year's actual figure &mdash; e.g., 2005 production is divided by
            present-day land area, not 2005's. Arable land changes slowly, so the effect is
            minor.<br><br>
            Source: FAOSTAT (QCL domain), World Bank (AG.LND.ARBL.HA) &mdash; both may revise
            figures in later releases.
            </span>
            </div>
            """,
            unsafe_allow_html=True,
        )
