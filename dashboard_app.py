"""
Streamlit version of the FAO Global Crop Yields dashboard, styled and arranged
to mimic the original Tableau layout: title top-left, Top Yields line (full
width, direct labels, no legend), Global Yield choropleth (full width), Top
Crops bar (full width, rank-graded color), a two-up row (crop bubble scatter +
the new Production-per-Arable-Hectare chart, in the old "Countries Sampled"
slot), and a bottom row pairing a text callout with the Yield/Area chart.

Reads the corrected pipeline outputs directly - no Tableau, no extract.
Run with: streamlit run dashboard_app.py
"""
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import pycountry
import streamlit as st

st.set_page_config(page_title="Global Crop Yields", layout="wide", page_icon="🌾")

# --- Palette: colors sampled directly (pixel-picked) from archive/tableau/Global
# Crop Yields.png so this matches the original workbook, not an approximation of
# it. SURFACE (#333333) is the original's actual panel/chart background, sampled
# from its "Top Yields" panel. The original uses this same color for the page
# background too (one flat tone, no separate "card" contrast), so BG matches it
# rather than the near-black shade used before. SEQ_RAMP is the original's
# choropleth legend gradient, sampled stop-by-stop. LINE_COLORS is the bold
# 4-color green->teal set the original reuses for the Top Yields lines and bars.
BG = "#333333"
SURFACE = "#333333"
TEXT = "#f5f5f5"
MUTED = "#b7b7b2"
GRID = "#4a4a4a"
SEQ_RAMP = ["#feffd9", "#f7fcc9", "#eff9b8", "#e0f4b3", "#ceeeb2", "#b5e4b3", "#95d7b7", "#77cbbc", "#5cc1c0", "#41b7c4"]
LINE_COLORS = ["#c7f296", "#94e7a8", "#51d2bb", "#27aab0"]

ISO3_OVERRIDES = {
    "Bolivia (Plurinational State of)": "BOL",
    "China; Hong Kong SAR": "HKG", "China; Macao SAR": "MAC",
    "China; Taiwan Province of": "TWN", "China; mainland": "CHN",
    "Democratic Republic of the Congo": "COD",
    "Iran (Islamic Republic of)": "IRN",
    "Micronesia (Federated States of)": "FSM",
    "Netherlands (Kingdom of the)": "NLD",
    "Venezuela (Bolivarian Republic of)": "VEN",
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
}

# Same "top yield countries" cohort as the original workbook's calculated field
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


@st.cache_data
def load_data():
    crops = pd.read_csv("FAO_Crop_Yield_TableauReady.csv")
    crops["DisplayCountry"] = crops["Country"].replace(DISPLAY_NAME_OVERRIDES)
    crops["ISO3"] = crops["Country"].apply(to_iso3)

    productivity = pd.read_csv("FAO_Arable_Land_Productivity.csv")
    productivity["DisplayCountry"] = productivity["Country"].replace(DISPLAY_NAME_OVERRIDES)
    return crops, productivity


crops, productivity = load_data()

st.markdown(
    f"""
    <style>
    .stApp {{ background-color: {BG}; }}
    h1, h2, h3, .stMarkdown, .stCaption, p {{ color: {TEXT} !important; }}
    .panel-title {{
        font-size: 14px; font-weight: 600; color: {MUTED}; margin: 6px 0 2px 0;
    }}
    .callout-box {{
        background-color: {SURFACE}; border-radius: 4px; padding: 18px 20px;
        height: 100%; font-size: 13.5px; line-height: 1.7; color: {TEXT};
    }}
    .callout-box b {{ color: #ffffff; }}
    .callout-box .insight {{ color: {MUTED}; font-size: 12.5px; margin-top: 14px; display:block; }}
    div[data-testid="stMetric"] {{ background-color: transparent; padding: 0; }}
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    f"<div style='font-size:32px; font-weight:700; color:{TEXT}; margin-bottom:2px;'>Global Crop Yields</div>",
    unsafe_allow_html=True,
)
st.caption(
    "Streamlit rebuild, same corrected FAO/World Bank data as the Tableau workbook — "
    "no export step, no extract, reads the CSVs directly."
)

# ---------------------------------------------------------------------------
# Controls — inline instead of in a sidebar, to match the original's single-
# page layout (no left rail in the Tableau workbook).
# ---------------------------------------------------------------------------
ctrl1, ctrl2, ctrl3 = st.columns([1, 1, 1.4])
with ctrl1:
    year = st.slider("Year", int(crops["Year"].min()), int(crops["Year"].max()), 2022)
with ctrl2:
    top_n = st.slider("Top N (bar charts)", 5, 30, 10)
all_countries = sorted(crops["DisplayCountry"].dropna().unique())
default_country = "Netherlands" if "Netherlands" in all_countries else all_countries[0]
with ctrl3:
    focus_country = st.selectbox(
        "Country (bottom chart)", all_countries, index=all_countries.index(default_country)
    )

year_crops = crops[crops["Year"] == year]

# ---------------------------------------------------------------------------
# Panel 1 — Top Yields (full width, direct end-of-line labels, no legend)
# ---------------------------------------------------------------------------
st.markdown("<div class='panel-title'>Top Yields</div>", unsafe_allow_html=True)
top_countries_now = (
    crops[crops["Year"] == crops["Year"].max()]
    .groupby("DisplayCountry")["Yield_tonha"].mean()
    .sort_values(ascending=False).head(4).index.tolist()
)
trend = crops[
    (crops["DisplayCountry"].isin(top_countries_now)) & (crops["Year"] >= 2012)
].groupby(["DisplayCountry", "Year"], as_index=False)["Yield_tonha"].mean()

fig = go.Figure()
for i, c in enumerate(top_countries_now):
    sub = trend[trend["DisplayCountry"] == c].sort_values("Year")
    color = LINE_COLORS[i % len(LINE_COLORS)]
    fig.add_trace(go.Scatter(
        x=sub["Year"], y=sub["Yield_tonha"], mode="lines", name=c,
        line=dict(color=color, width=2, dash="dot"), showlegend=False,
    ))
    fig.add_annotation(
        x=sub["Year"].iloc[-1], y=sub["Yield_tonha"].iloc[-1], text=f"  {c}",
        showarrow=False, xanchor="left", font=dict(color=color, size=12),
    )
fig.update_layout(
    plot_bgcolor=SURFACE, paper_bgcolor=SURFACE, font_color=TEXT,
    margin=dict(l=10, r=110, t=10, b=10), height=300,
    yaxis_title="Avg. Yield (t/ha)",
)
fig.update_xaxes(gridcolor=GRID, showgrid=False)
fig.update_yaxes(gridcolor=GRID)
st.plotly_chart(fig, width='stretch')
st.caption(
    "Simple average across every crop a country reports — countries with a narrow, "
    "high-yield crop mix (e.g. greenhouse-heavy) can look like outliers."
)


# ---------------------------------------------------------------------------
# Panel 2 — Global Yield choropleth (full width)
# ---------------------------------------------------------------------------
st.markdown(f"<div class='panel-title'>Global Yield {year}</div>", unsafe_allow_html=True)
map_df = (
    year_crops.dropna(subset=["ISO3", "Yield_tonha"])
    .groupby(["ISO3", "DisplayCountry"], as_index=False)["Yield_tonha"].mean()
)
fig = px.choropleth(
    map_df, locations="ISO3", color="Yield_tonha", hover_name="DisplayCountry",
    color_continuous_scale=SEQ_RAMP, labels={"Yield_tonha": "Avg yield (t/ha)"},
)
fig.update_layout(
    geo=dict(bgcolor=SURFACE, lakecolor=SURFACE, landcolor="#454545", showframe=False,
              showcountries=True, countrycolor="#262626"),
    paper_bgcolor=SURFACE, font_color=TEXT,
    margin=dict(l=0, r=0, t=10, b=0), height=380,
    coloraxis_colorbar=dict(orientation="h", y=-0.05, len=0.35, x=0.02, xanchor="left", thickness=12),
)
st.plotly_chart(fig, width='stretch')
st.caption(
    "Simple average across every crop a country reports — countries with a narrow, "
    "high-yield crop mix (e.g. greenhouse-heavy) can look like outliers."
)


# ---------------------------------------------------------------------------
# Panel 3 — Top Crops bar (full width, rank-graded color to match the original)
# ---------------------------------------------------------------------------
st.markdown(f"<div class='panel-title'>Top Crops {year}</div>", unsafe_allow_html=True)
top_crops = (
    year_crops.groupby("Crop", as_index=False)["Production_tons"].sum()
    .sort_values("Production_tons", ascending=False).head(top_n)
)
top_crops = top_crops.sort_values("Production_tons")
rank_colors = px.colors.sample_colorscale(LINE_COLORS, [i / (len(top_crops) - 1) for i in range(len(top_crops))]) if len(top_crops) > 1 else LINE_COLORS[:1]

fig = go.Figure(go.Bar(
    x=top_crops["Production_tons"], y=top_crops["Crop"], orientation="h",
    marker=dict(color=rank_colors),
))
fig.update_layout(
    plot_bgcolor=SURFACE, paper_bgcolor=SURFACE, font_color=TEXT,
    margin=dict(l=10, r=10, t=10, b=10), height=340,
    xaxis_title="Production (t)",
)
fig.update_xaxes(gridcolor=GRID)
fig.update_yaxes(gridcolor=GRID)
st.plotly_chart(fig, width='stretch')
st.caption("No livestock items, no FAO rollup categories double-counting their own constituents.")


# ---------------------------------------------------------------------------
# Panel 4 — two-up row: Crop bubble scatter | Production per Arable Hectare
# (this replaces the original's "Countries Sampled" text-wall slot)
# ---------------------------------------------------------------------------
col3, col4 = st.columns([1, 1])

with col3:
    st.markdown(f"<div class='panel-title'>Top Crops / Area {year}</div>", unsafe_allow_html=True)
    bubble = (
        year_crops.groupby("Crop", as_index=False)
        .agg(Production_tons=("Production_tons", "sum"),
             AreaHarvested_ha=("AreaHarvested_ha", "sum"),
             Countries=("Country", "nunique"))
    )
    bubble = bubble.sort_values("Production_tons", ascending=False).head(25)
    label_set = set(bubble.sort_values("Production_tons", ascending=False).head(4)["Crop"])
    # Hollow/outlined bubbles (transparent fill, colored stroke) to match the
    # original's circle-outline style, rather than px.scatter's solid fill.
    size_max = 38
    sizeref = 2. * bubble["Countries"].max() / (size_max ** 2)
    fig = go.Figure(go.Scatter(
        x=bubble["AreaHarvested_ha"], y=bubble["Production_tons"],
        mode="markers", text=bubble["Crop"],
        marker=dict(
            size=bubble["Countries"], sizemode="area", sizeref=sizeref, sizemin=4,
            color="rgba(0,0,0,0)",
            line=dict(color=bubble["Production_tons"], colorscale=LINE_COLORS, width=2),
        ),
        hovertemplate="<b>%{text}</b><br>Cultivated Area: %{x:,.0f} ha<br>"
                      "Production: %{y:,.0f} t<extra></extra>",
    ))
    for _, row in bubble[bubble["Crop"].isin(label_set)].iterrows():
        fig.add_annotation(x=row["AreaHarvested_ha"], y=row["Production_tons"], text=row["Crop"],
                            showarrow=False, yshift=14, font=dict(color=TEXT, size=11))
    fig.update_layout(
        plot_bgcolor=SURFACE, paper_bgcolor=SURFACE, font_color=TEXT,
        margin=dict(l=10, r=10, t=10, b=10), height=380,
        xaxis_title="Cultivated Area (ha)", yaxis_title="Production (t)",
    )
    fig.update_xaxes(gridcolor=GRID)
    fig.update_yaxes(gridcolor=GRID)
    st.plotly_chart(fig, width='stretch')
    st.caption("Bubble size = number of countries growing that crop.")

with col4:
    st.markdown(f"<div class='panel-title'>Production per Arable Hectare {year}</div>", unsafe_allow_html=True)
    prod_year = (
        productivity[productivity["Year"] == year]
        .sort_values("ProductionPerArableHa_tons", ascending=False).head(top_n)
        .sort_values("ProductionPerArableHa_tons")
    )
    fig = go.Figure(go.Bar(
        x=prod_year["ProductionPerArableHa_tons"], y=prod_year["DisplayCountry"], orientation="h",
        marker=dict(color=LINE_COLORS[2]),
    ))
    fig.update_layout(
        plot_bgcolor=SURFACE, paper_bgcolor=SURFACE, font_color=TEXT,
        margin=dict(l=10, r=10, t=10, b=10), height=380,
        xaxis_title="Production / arable ha (t)",
    )
    fig.update_xaxes(gridcolor=GRID)
    fig.update_yaxes(gridcolor=GRID)
    st.plotly_chart(fig, width='stretch')
    st.caption(
        "What each country produces relative to the arable land it has — missing from the original. "
        "Note: World Bank arable land excludes land under permanent crops (palm oil, bananas, coffee, "
        "cocoa), while production includes them — this structurally favors tree-crop economies over "
        "genuinely land-productive farming."
    )


# ---------------------------------------------------------------------------
# Panel 5 — bottom row: text callout (left) | Yield/Area by Country (right)
# ---------------------------------------------------------------------------
col5, col6 = st.columns([1, 2.2])

top_2012 = crops[(crops["Country"].isin(TOP_YIELD_COUNTRIES)) & (crops["Year"] == 2012)]["Yield_tonha"].mean()
top_2022 = crops[(crops["Country"].isin(TOP_YIELD_COUNTRIES)) & (crops["Year"] == 2022)]["Yield_tonha"].mean()
glob_2012 = crops[crops["Year"] == 2012]["Yield_tonha"].mean()
glob_2022 = crops[crops["Year"] == 2022]["Yield_tonha"].mean()
top_pct = (top_2022 / top_2012 - 1) * 100
glob_pct = (glob_2022 / glob_2012 - 1) * 100

with col5:
    st.markdown(f"<div class='panel-title'>Country: {focus_country}</div>", unsafe_allow_html=True)
    st.markdown(
        f"""
        <div class="callout-box">
        <b>Top Yield Countries:</b><br>
        2012 Avg. Yield: {top_2012:.1f} t/ha<br>
        2022 Avg. Yield: {top_2022:.1f} t/ha<br>
        Increase: {top_pct:+.1f}%<br>
        <br>
        <b>Global Avg.:</b><br>
        2012 Avg. Yield: {glob_2012:.1f} t/ha<br>
        2022 Avg. Yield: {glob_2022:.1f} t/ha<br>
        Increase: {glob_pct:+.1f}%
        <span class="insight">Top producers have improved yield faster than the global average, widening the
        efficiency gap. The area considered is cultivated area, not total country area.<br><br>
        Source: FAOSTAT, World Bank.</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

with col6:
    st.markdown("<div class='panel-title'>Yield / Area by Country</div>", unsafe_allow_html=True)
    focus_row_country = crops.loc[crops["DisplayCountry"] == focus_country, "Country"].iloc[0]
    country_series = (
        crops[crops["Country"] == focus_row_country]
        .groupby("Year", as_index=False)
        .agg(AreaHarvested_ha=("AreaHarvested_ha", "sum"), Yield_tonha=("Yield_tonha", "mean"))
    )
    # One combined dual-axis panel (Area bars + Yield line sharing the same plot
    # area, like the original) instead of two stacked subplots.
    fig = go.Figure()
    fig.add_trace(go.Bar(x=country_series["Year"], y=country_series["AreaHarvested_ha"],
                          marker_color=LINE_COLORS[0], name="Area (ha)", yaxis="y1"))
    fig.add_trace(go.Scatter(x=country_series["Year"], y=country_series["Yield_tonha"],
                              mode="lines+markers", marker=dict(symbol="x", size=7),
                              line=dict(color=SEQ_RAMP[-1], width=2), name="Average Yield",
                              yaxis="y2"))
    fig.update_layout(
        plot_bgcolor=SURFACE, paper_bgcolor=SURFACE, font_color=TEXT,
        margin=dict(l=10, r=10, t=10, b=10), height=380,
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        xaxis=dict(gridcolor=GRID),
        yaxis=dict(title="Area (ha)", gridcolor=GRID),
        yaxis2=dict(title="Avg. Yield (t/ha)", overlaying="y", side="right", showgrid=False),
    )
    st.plotly_chart(fig, width='stretch')
