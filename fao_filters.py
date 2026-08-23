"""
Shared filtering/correction constants for the FAO pipeline.

Used by pipeline.py, which runs the full merge against the raw FAOSTAT bulk
export when it's available and falls back to reapplying these same
corrections directly to the already-produced output CSV otherwise.
"""
import re

# ---------------------------------------------------------------------------
# Scope: this pipeline reports on CROPS only. FAOSTAT's Production_Crops_Livestock
# domain also carries livestock and animal-derived products (meat, milk, eggs,
# hides, rendered fat, etc.) under the same Element names ("Production", "Yield"),
# so they must be excluded explicitly rather than assumed away by the Element filter.
# This is a keyword denylist on the item name, not an official FAO classification
# table (none was available locally) - extend it if new animal-derived items appear.
# ---------------------------------------------------------------------------
LIVESTOCK_KEYWORDS = [
    "meat", "milk", "egg", "wool", "hide", "skin", "honey", "beeswax", "cocoon",
    "silk", "cattle", "buffalo", "goat", "sheep", "pig", "swine", "poultry",
    "chicken", "duck", "turkey", "goose", "geese", "horse", "ass", "mule",
    "camel", "rabbit", "snail", "offal", "fat", "butter", "cheese", "lard",
    "tallow", "cream", "yoghurt", "casein", "whey",
]
livestock_pattern = re.compile("|".join(LIVESTOCK_KEYWORDS), re.IGNORECASE)

# Keyword substring matching is deliberately broad (it needs to catch plurals
# and compounds like "Buttermilk" or "Camels" that a strict \bword\b match
# would miss), but that same looseness produces real false positives on
# genuine crop names that happen to contain a keyword mid-word - a full pass
# over every item the pattern matches turned up 9 actual crops wrongly
# caught, most severely "Cassava; fresh" and "Cassava leaves" (cASSava, via
# "ass") - one of the world's top staple root crops was silently missing
# from every output this whole project has produced.
# Tightening the regex with word boundaries was tried first and rejected:
# it fixes these cases but also breaks "Camels" -> "camel", "Goats" ->
# "goat", "Eggs Primary" -> "egg" and every other plural, which are supposed
# to match. There's no single regex that gets both right, so this is an
# explicit exception list instead - same approach already used for "Natural
# rubber in primary forms" not being treated as a rollup despite the name.
LIVESTOCK_FALSE_POSITIVES = {
    "Cassava; fresh", "Cassava leaves",         # "ass" in cASSava
    "Eggplants (aubergines)",                   # "egg" in EGGplants
    "Gooseberries",                              # "goose" in GOOSEberries
    "Pigeon peas; dry",                          # "pig" in PIGeon
    "Tallowtree seeds",                          # "tallow" in TALLOWtree
    "Molasses",                                  # "ass" in molASSes
    "Broad beans and horse beans; dry",          # "horse bean" = fava bean,
    "Broad beans and horse beans; green",        # an old common name, not equine
}

# ---------------------------------------------------------------------------
# Same problem as the Area field (below), one level down: FAOSTAT's Item field
# also mixes individual crops with its own rollup categories - "Cereals; primary"
# is the sum of Maize + Wheat + Rice + every other cereal, "Vegetables Primary" /
# "Fruit Primary" / "Roots and Tubers; Total" / "Sugar Crops Primary" /
# "Pulses; Total" / "Treenuts; Total" / "Citrus Fruit; Total" work the same way,
# and "Oilcrops; Cake Equivalent" / "Oilcrops; Oil Equivalent" / "Fibre Crops;
# Fibre Equivalent" are unit-converted rollups of the same kind. Left in, any
# cross-crop total (a country's total production, or this pipeline's own
# arable-land productivity metric) double-counts every crop that has a parent
# category. "Natural rubber in primary forms" looks similar but isn't a rollup
# (it's a specific commodity - raw/primary-processed rubber) and stays.
# ---------------------------------------------------------------------------
AGGREGATE_ITEMS = {
    "Cereals; primary", "Citrus Fruit; Total", "Fibre Crops; Fibre Equivalent",
    "Fruit Primary", "Oilcrops; Cake Equivalent", "Oilcrops; Oil Equivalent",
    "Pulses; Total", "Roots and Tubers; Total", "Sugar Crops Primary",
    "Treenuts; Total", "Vegetables Primary",
}

# ---------------------------------------------------------------------------
# FAOSTAT's Area field mixes real countries with continental/regional rollups,
# economic groupings, and "World" - each one a sum over the real countries it
# contains. Left in, any cross-country total (e.g. global production of a
# crop) silently multi-counts: summing every Area row for maize in 2022 comes
# to ~5.28B tons, vs. ~1.28B tons summing only real countries (the FAO "World"
# row itself reports ~1.17B) - a ~4x inflation from World + continents +
# sub-regions + the "China" parent (which duplicates its own "China; mainland"
# / "China; Hong Kong SAR" / "China; Macao SAR" / "China; Taiwan Province of"
# rows) all being counted alongside the countries they aggregate.
# Dissolved/historical entities (e.g. "Sudan (former)", "Serbia and Montenegro")
# are NOT excluded here - they don't temporally overlap with their successor
# countries, so they don't double-count a given year's total.
# ---------------------------------------------------------------------------
AGGREGATE_AREAS = {
    "Africa", "Americas", "Asia", "Australia and New Zealand", "Caribbean",
    "Central America", "Central Asia", "China", "Eastern Africa", "Eastern Asia",
    "Eastern Europe", "Europe", "European Union (27)", "Land Locked Developing Countries",
    "Least Developed Countries", "Low Income Food Deficit Countries", "Melanesia",
    "Micronesia", "Middle Africa", "Net Food Importing Developing Countries",
    "Northern Africa", "Northern America", "Northern Europe", "Oceania",
    "Polynesia", "Small Island Developing States", "South America",
    "South-eastern Asia", "Southern Africa", "Southern Asia", "Southern Europe",
    "Western Africa", "Western Asia", "Western Europe", "World",
}

# ---------------------------------------------------------------------------
# World Bank arable-land data (arable_land_ha.csv) names countries differently
# than FAO does - this reconciles the two before joining. China is a
# many-to-one approximation (WB reports one figure, FAO splits mainland/HK/
# Macao/Taiwan) - mapped to mainland only (>99% of China's arable land is
# there), leaving Hong Kong, Macao, and Taiwan deliberately unmatched rather
# than misassigned the mainland's arable-land figure.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Crops with no reliable kcal factor - see derive_value_kcal.py's own
# docstring for the full rationale (each one converts almost entirely into a
# DIFFERENT, separately-tracked FBS item - sugar, oil, beer - that script
# never looks up, so the raw item's own Food/Production share is a near-zero
# artifact of that split, not a genuine measure of food use). Shared here
# because dashboard_app.py also needs this exact set: the "$ vs kcal per
# Arable Hectare" panel's Value axis has no equivalent exclusion (FAOSTAT's
# Value of Production covers these crops just fine), so any country whose
# top crop is one of these is mechanically pushed toward the value/ROI end
# of that panel regardless of its real food-vs-cash-crop balance - true for
# Malaysia (Oil palm fruit, ~70% of its harvested area) and Mauritius/
# Barbados/Cabo Verde (Sugar cane). Disclosed on that panel's own
# caption rather than silently fixed - excluding these from the Value side
# too would just trade this bias for the EU per-crop coverage gap (see
# derive_eu_value_gap.py), since a scoped-down Value figure can't use the
# aggregate-basket fallback either.
# ---------------------------------------------------------------------------
KCAL_EXCLUDE_ITEMS = {
    "Oil palm fruit", "Sugar cane", "Sugar beet", "Other sugar crops n.e.c.",
    "Soya beans", "Rape or colza seed", "Sunflower seed",
    "Cotton seed", "Seed cotton; unginned", "Barley",
}

WB_TO_FAO_COUNTRY = {
    "Bahamas, The": "Bahamas",
    "Bolivia": "Bolivia (Plurinational State of)",
    "China": "China; mainland",
    # These two showed gray on the dashboard's value-per-arable-hectare maps
    # despite having FAOSTAT crop data: the World Bank arable-land table does
    # carry them, just under a name this mapping didn't have an entry for
    # yet. Taiwan, Cook Islands, and Niue were checked too and are genuinely
    # absent from that table (no entry under any name) - not a mapping bug.
    "Hong Kong SAR, China": "China; Hong Kong SAR",
    "Puerto Rico (US)": "Puerto Rico",
    "Congo, Dem. Rep.": "Democratic Republic of the Congo",
    "Congo, Rep.": "Congo",
    "Cote d'Ivoire": "Côte d'Ivoire",
    "Egypt, Arab Rep.": "Egypt",
    "Gambia, The": "Gambia",
    "Iran, Islamic Rep.": "Iran (Islamic Republic of)",
    "Korea, Dem. People's Rep.": "Democratic People's Republic of Korea",
    "Korea, Rep.": "Republic of Korea",
    "Kyrgyz Republic": "Kyrgyzstan",
    "Laos": "Lao People's Democratic Republic",
    "Lao PDR": "Lao People's Democratic Republic",
    "Micronesia, Fed. Sts.": "Micronesia (Federated States of)",
    "Moldova": "Republic of Moldova",
    "Netherlands": "Netherlands (Kingdom of the)",
    "Slovak Republic": "Slovakia",
    "St. Kitts and Nevis": "Saint Kitts and Nevis",
    "St. Lucia": "Saint Lucia",
    "St. Vincent and the Grenadines": "Saint Vincent and the Grenadines",
    "Tanzania": "United Republic of Tanzania",
    "Turkiye": "Türkiye",
    "United Kingdom": "United Kingdom of Great Britain and Northern Ireland",
    "United States": "United States of America",
    "Venezuela, RB": "Venezuela (Bolivarian Republic of)",
    "Yemen, Rep.": "Yemen",
    "Somalia, Fed. Rep.": "Somalia",
    "West Bank and Gaza": "Palestine",
}
