"""
pages/04_target_vs_actual.py
----------------------------
Target vs. Actual Performance - Bonus Page for ChipMetrics.

Joins enriched order data to regional_targets on:
    region + product_family + fiscal_quarter (derived from order_date)

Data quality fixes applied in this file:
- revenue_target_usd: strip commas and cast to float
- Region names normalized (AMER -> Americas, Asia-Pacific -> APAC, JP -> Japan)
- Product family casing normalized to title case
- Fiscal quarter formats normalized to YYYY-QN
- fiscal_quarter derived from order_date (not in raw CSV)
"""

import re
import pandas as pd
import streamlit as st
import plotly.express as px
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from src.data_loader import load_all_data

st.set_page_config(
    page_title="ChipMetrics - Target vs. Actual",
    page_icon="[Target]",
    layout="wide",
)

COLORS = {
    "green":   "#24A148",
    "yellow":  "#F1C21B",
    "red":     "#DA1E28",
    "grey":    "#6F6F6F",
    "bg":      "#F4F4F4",
    "primary": "#0F62FE",
}

CHART_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="IBM Plex Sans, sans-serif", color="#161616"),
    margin=dict(l=24, r=24, t=48, b=24),
)

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
dfs, _ = load_all_data()

orders_raw = dfs.get("orders")
catalog    = dfs.get("catalog")
targets    = dfs.get("targets")

if orders_raw is None:
    st.error("customer_orders.csv could not be loaded.")
    st.stop()

if targets is None:
    st.error("regional_targets.csv could not be loaded. This page requires target data.")
    st.stop()

# Work on copies so we do not mutate cached dataframes
targets = targets.copy()

# Fix revenue_target_usd - stored as string with commas in some rows
# e.g. "3,408,643" vs "1672224" - strip commas and cast to float
targets["revenue_target_usd"] = pd.to_numeric(
    targets["revenue_target_usd"].astype(str).str.replace(",", "", regex=False),
    errors="coerce"
)

# ---------------------------------------------------------------------------
# Normalize quarter format to YYYY-QN
# Handles: 2024Q3, FY2025-Q1, Q4-FY2025
# ---------------------------------------------------------------------------
def normalize_quarter(q):
    if pd.isna(q):
        return None
    q = str(q).strip()
    m = re.match(r"(\d{4})Q(\d)", q)
    if m:
        return f"{m.group(1)}-Q{m.group(2)}"
    m = re.match(r"FY(\d{4})-Q(\d)", q)
    if m:
        return f"{m.group(1)}-Q{m.group(2)}"
    m = re.match(r"Q(\d)-FY(\d{4})", q)
    if m:
        return f"{m.group(2)}-Q{m.group(1)}"
    return q

# ---------------------------------------------------------------------------
# Build actuals
# ---------------------------------------------------------------------------

# Enrich orders with product_family from catalog
if catalog is not None:
    orders = orders_raw.merge(
        catalog[["sku", "product_family"]], on="sku", how="left"
    )
else:
    orders = orders_raw.copy()
    orders["product_family"] = "Unknown"

orders["product_family"] = orders["product_family"].fillna("Unknown")

# Exclude cancelled orders
orders_active = orders[orders["order_status"].str.lower() != "cancelled"].copy()

# Normalize region names
orders_active["region"] = orders_active["region"].replace({
    "AMER": "Americas",
    "Asia-Pacific": "APAC",
    "JP": "Japan",
})

# Normalize product family casing
orders_active["product_family"] = orders_active["product_family"].str.strip().str.title()
targets["product_family"] = targets["product_family"].str.strip().str.title()

# Derive fiscal_quarter from order_date
orders_active["order_date"] = pd.to_datetime(orders_active["order_date"], errors="coerce")
orders_active["fiscal_quarter"] = (
    orders_active["order_date"].dt.to_period("Q").astype(str)
)

# Normalize quarter formats on both sides
orders_active["fiscal_quarter"] = orders_active["fiscal_quarter"].apply(normalize_quarter)
targets["fiscal_quarter"] = targets["fiscal_quarter"].apply(normalize_quarter)

# Aggregate actuals
actuals = (
    orders_active
    .groupby(["region", "product_family", "fiscal_quarter"], dropna=False)["total_line_value"]
    .sum()
    .reset_index()
    .rename(columns={"total_line_value": "actual_revenue"})
)

# ---------------------------------------------------------------------------
# Join actuals to targets - OUTER JOIN
# ---------------------------------------------------------------------------
merged = actuals.merge(
    targets[[
        "region", "product_family", "fiscal_quarter",
        "revenue_target_usd", "units_target", "asp_target"
    ]],
    on=["region", "product_family", "fiscal_quarter"],
    how="outer",
)

merged["actual_revenue"] = merged["actual_revenue"].fillna(0.0)

# ---------------------------------------------------------------------------
# Attainment calculation
# ---------------------------------------------------------------------------
def compute_attainment(row):
    if pd.isna(row["revenue_target_usd"]) or row["revenue_target_usd"] <= 0:
        return None
    return row["actual_revenue"] / row["revenue_target_usd"] * 100

merged["attainment_pct"] = merged.apply(compute_attainment, axis=1)

def attainment_band(pct):
    if pct is None or pd.isna(pct):
        return "No Target"
    if pct >= 100:
        return ">= 100% (On/Above Target)"
    if pct >= 75:
        return "75-99% (Near Target)"
    return "<75% (Below Target)"

merged["band"] = merged["attainment_pct"].apply(attainment_band)

# ---------------------------------------------------------------------------
# Sidebar filters
# ---------------------------------------------------------------------------
st.sidebar.header("Filters")

available_quarters = sorted(merged["fiscal_quarter"].dropna().unique().tolist())
selected_quarters = st.sidebar.multiselect(
    "Fiscal Quarter", options=available_quarters, default=available_quarters
)
if not selected_quarters:
    selected_quarters = available_quarters

available_regions = sorted(merged["region"].dropna().unique().tolist())
selected_regions = st.sidebar.multiselect(
    "Region", options=available_regions, default=available_regions
)
if not selected_regions:
    selected_regions = available_regions

mask = (
    merged["fiscal_quarter"].isin(selected_quarters)
    & merged["region"].isin(selected_regions)
)
df = merged[mask].copy()

# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------
st.title("Target vs. Actual Performance")
st.caption(
    "Compares booked order revenue against quarterly sales targets "
    "by region and product family. Cancelled orders are excluded from actuals."
)
st.divider()

if df.empty:
    st.warning("No data matches the current filter selection.")
    st.stop()

# ---------------------------------------------------------------------------
# KPI row
# ---------------------------------------------------------------------------
has_target   = df["revenue_target_usd"].notna() & (df["revenue_target_usd"] > 0)
df_targeted  = df[has_target]
df_no_target = df[~has_target]

total_actual  = df_targeted["actual_revenue"].sum()
total_target  = df_targeted["revenue_target_usd"].sum()
overall_att   = total_actual / total_target * 100 if total_target > 0 else 0.0

n_above_target = (df_targeted["attainment_pct"] >= 100).sum()
n_at_risk      = (df_targeted["attainment_pct"] < 75).sum()

k1, k2, k3, k4 = st.columns(4)
with k1:
    st.metric(
        "Overall Attainment",
        f"{overall_att:.1f}%",
        delta=f"{overall_att - 100:.1f}pp vs target",
        delta_color="normal" if overall_att >= 100 else "inverse",
    )
with k2:
    st.metric("Total Actual Revenue", f"${total_actual:,.0f}")
with k3:
    st.metric("Combinations >= 100%", f"{n_above_target}")
with k4:
    st.metric(
        "Combinations < 75%",
        f"{n_at_risk}",
        delta="Needs attention" if n_at_risk > 0 else "None",
        delta_color="inverse" if n_at_risk > 0 else "normal",
    )

st.divider()

# ---------------------------------------------------------------------------
# Attainment table
# ---------------------------------------------------------------------------
st.subheader("Attainment by Region / Product Family / Quarter")

BAND_LABEL = {
    ">= 100% (On/Above Target)": "On/Above Target",
    "75-99% (Near Target)":      "Near Target",
    "<75% (Below Target)":       "Below Target",
    "No Target":                 "No Target",
}

display = df[[
    "region", "product_family", "fiscal_quarter",
    "revenue_target_usd", "actual_revenue", "attainment_pct", "band",
]].copy().sort_values(["fiscal_quarter", "region", "product_family"])

display["revenue_target_usd"] = display["revenue_target_usd"].apply(
    lambda x: f"${x:,.0f}" if pd.notna(x) else "--"
)
display["actual_revenue"] = display["actual_revenue"].apply(lambda x: f"${x:,.0f}")
display["attainment_pct"] = display["attainment_pct"].apply(
    lambda x: f"{x:.1f}%" if pd.notna(x) else "--"
)
display["band"] = display["band"].apply(lambda b: BAND_LABEL.get(b, b))
display.columns = [
    "Region", "Product Family", "Quarter",
    "Target Revenue", "Actual Revenue", "Attainment %", "Status",
]
display.index = range(1, len(display) + 1)
st.dataframe(display, use_container_width=True)

# ---------------------------------------------------------------------------
# Attainment bar chart
# ---------------------------------------------------------------------------
if not df_targeted.empty:
    st.divider()
    st.subheader("Attainment % by Combination")
    st.caption("Only region/product-family/quarter combinations with a defined target are shown.")

    chart_df = df_targeted.copy()
    chart_df["label"] = (
        chart_df["region"] + " - "
        + chart_df["product_family"] + " - "
        + chart_df["fiscal_quarter"].fillna("?")
    )
    chart_df = chart_df.sort_values("attainment_pct", ascending=True)

    fig = px.bar(
        chart_df,
        x="attainment_pct",
        y="label",
        orientation="h",
        color="band",
        color_discrete_map={
            ">= 100% (On/Above Target)": COLORS["green"],
            "75-99% (Near Target)":      COLORS["yellow"],
            "<75% (Below Target)":       COLORS["red"],
        },
        text=chart_df["attainment_pct"].apply(lambda x: f"{x:.0f}%"),
        title="Revenue Attainment % by Region / Product Family / Quarter",
        custom_data=["actual_revenue", "revenue_target_usd"],
    )
    fig.add_vline(
        x=100,
        line_dash="dash",
        line_color="#161616",
        annotation_text="100% target",
        annotation_position="top right",
    )
    fig.add_vline(
        x=75,
        line_dash="dot",
        line_color=COLORS["red"],
        annotation_text="75% floor",
        annotation_position="bottom right",
    )
    fig.update_traces(
        textposition="outside",
        hovertemplate=(
            "<b>%{y}</b><br>"
            "Attainment: %{x:.1f}%<br>"
            "Actual: $%{customdata[0]:,.0f}<br>"
            "Target: $%{customdata[1]:,.0f}"
            "<extra></extra>"
        ),
    )
    fig.update_layout(
        **CHART_LAYOUT,
        xaxis_title="Attainment (%)",
        yaxis_title="",
        legend_title="Band",
        xaxis_ticksuffix="%",
    )
    st.plotly_chart(fig, use_container_width=True)

# ---------------------------------------------------------------------------
# Combinations with no target data
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Combinations With No Target Data")

if df_no_target.empty:
    st.success("All region/product-family/quarter combinations have target data.")
else:
    st.warning(
        f"{len(df_no_target)} combination(s) have actual order revenue but no target defined. "
        "These are excluded from attainment calculations - consider adding targets for them."
    )
    no_target_display = df_no_target[[
        "region", "product_family", "fiscal_quarter", "actual_revenue"
    ]].copy()
    no_target_display["actual_revenue"] = no_target_display["actual_revenue"].apply(
        lambda x: f"${x:,.0f}"
    )
    no_target_display.columns = ["Region", "Product Family", "Quarter", "Actual Revenue"]
    no_target_display = no_target_display.sort_values(
        ["Quarter", "Region", "Product Family"]
    ).reset_index(drop=True)
    no_target_display.index = range(1, len(no_target_display) + 1)
    st.dataframe(no_target_display, use_container_width=True)
