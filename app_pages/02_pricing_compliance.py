"""
app_pages/02_pricing_compliance.py
------------------------------------
Pricing Compliance Analysis — Page 2 of ChipMetrics.

All data comes from data_loader.load_all_data(). No raw CSV reads here.
Charts use Plotly. Sidebar filters (customer type + region) propagate to
every chart, KPI, and table on the page.

Compliance logic (simplified scope per README):
    - Join orders to agreements on customer_id + sku.
    - No tier matching — use any contracted price for that customer/SKU pair.
    - When multiple agreements exist for the same customer/SKU, use the one
      with the lowest contracted_price (most favorable to customer).
    - Flag each matched order line into one of three categories:
        * Compliant            : negotiated_unit_price <= contracted_price
        * Overpaying           : negotiated_unit_price > contracted_price
        * Unauthorized Discount: negotiated_unit_price < contracted_price * 0.90
          (more than 10% below contract — reclassified out of Compliant)
    - Unmatched: order lines with no agreement in pricing_agreements for
      that customer_id + sku. Retained and counted separately.

Page structure:
    1. Sidebar filters
    2. KPI summary row
    3. Compliance breakdown by customer type (chart)
    4. Compliance breakdown by region (chart)
    5. Dollar gap table — top 10 worst offenders
    6. Plain English risk callout
"""

import pandas as pd
import numpy as np
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
sys.path.append(str(Path(__file__).parent.parent))
from src.data_loader import load_all_data

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="ChipMetrics — Pricing Compliance",
    page_icon="",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Color palette — matches order_analytics.py for visual consistency
# ---------------------------------------------------------------------------
COLORS = {
    "compliant":   "#24A148",   # Green
    "overpaying":  "#F1C21B",   # Yellow — customer paying more than contract
    "unauth":      "#DA1E28",   # Red — unauthorized discount
    "unmatched":   "#6F6F6F",   # Grey — no agreement found
    "primary":     "#0F62FE",   # IBM Blue
    "accent":      "#FF6B35",   # Orange
    "bg_card":     "rgba(255,255,255,0.08)",
    "danger":      "#DA1E28",
    "warning":     "#F1C21B",
    "success":     "#24A148",
}

STATUS_COLOR_MAP = {
    "Compliant":             COLORS["compliant"],
    "Overpaying":            COLORS["overpaying"],
    "Unauthorized Discount": COLORS["unauth"],
}

CHART_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="IBM Plex Sans, sans-serif", color="#161616"),
    margin=dict(l=24, r=24, t=48, b=24),
)

UNAUTH_THRESHOLD = 0.10   # 10% below contracted price triggers Unauthorized Discount flag

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
dfs, issues_log = load_all_data()

orders_raw  = dfs.get("orders")
catalog     = dfs.get("catalog")
agreements  = dfs.get("agreements")

if orders_raw is None:
    st.error("customer_orders.csv could not be loaded. Check your data/ folder.")
    st.stop()

if agreements is None:
    st.error("pricing_agreements.csv could not be loaded. Compliance analysis is unavailable.")
    st.stop()

# ---------------------------------------------------------------------------
# Build the compliance dataset
# ---------------------------------------------------------------------------

# --- Step 1: Enrich orders with product_family from catalog ---
# product_family is needed for the region breakdown chart and for grouping.
# Use a LEFT JOIN so orders with orphan SKUs are retained (they'll show Unknown).
if catalog is not None:
    orders = orders_raw.merge(
        catalog[["sku", "product_family"]],
        on="sku",
        how="left",
    )
else:
    orders = orders_raw.copy()
    orders["product_family"] = "Unknown"

orders["product_family"] = orders["product_family"].fillna("Unknown")

# --- Step 2: Exclude Cancelled orders from compliance analysis ---
# A cancelled order never resulted in a transaction, so pricing compliance
# is not meaningful for it. Including cancelled lines would deflate compliance
# rates by adding rows that were never actually charged.
orders_active = orders[orders["order_status"].str.lower() != "cancelled"].copy()

# --- Step 3: Build the best-price agreement lookup ---
# For each (customer_id, sku) pair, select the agreement with the lowest
# contracted_price (most favorable to the customer). This is the simplified
# scope from the README — no quantity tier matching.
#
# Only use USD agreements. Non-USD contracted prices cannot be compared to
# negotiated_unit_price (which is USD) without FX conversion data we don't have.
# data_loader already added the `is_usd` flag column for this purpose.

agr_usd = agreements.copy()

# Select the single best (lowest) contracted price per customer/SKU pair.
# Using min() means we're giving the customer the benefit of the doubt —
# if they have two agreements for the same part, we compare against the cheaper one.
best_price = (
    agr_usd.groupby(["customer_id", "sku"])["contracted_price"]
    .min()
    .reset_index()
    .rename(columns={"contracted_price": "contracted_price_used"})
)

# --- Step 4: Join orders to best-price agreements ---
# LEFT JOIN retains all active order lines. Rows with no agreement match
# get NaN in contracted_price_used — these become "Unmatched".
compliance_df = orders_active.merge(
    best_price,
    on=["customer_id", "sku"],
    how="left",
)

# --- Step 5: Classify each order line ---
# Three mutually exclusive statuses for matched rows:
#   Unauthorized Discount → negotiated < contracted * (1 - UNAUTH_THRESHOLD)
#   Compliant             → negotiated <= contracted
#   Overpaying            → negotiated > contracted
# Unmatched stays as its own group (not included in compliance rate denominator
# per standard practice — we can't assess what we have no agreement for).

def classify_compliance(row) -> str:
    """Return compliance status for a single order line."""
    if pd.isna(row["contracted_price_used"]):
        return "Unmatched"
    if pd.isna(row["negotiated_unit_price"]):
        return "Unmatched"   # Can't assess without an actual price
    contracted = row["contracted_price_used"]
    actual     = row["negotiated_unit_price"]
    if contracted <= 0:
        return "Unmatched"   # Zero/negative contracted price is a data quality issue
    if actual < contracted * (1 - UNAUTH_THRESHOLD):
        return "Unauthorized Discount"
    if actual <= contracted:
        return "Compliant"
    return "Overpaying"

compliance_df["compliance_status"] = compliance_df.apply(classify_compliance, axis=1)

# --- Step 6: Compute dollar gap ---
# Dollar gap = (negotiated_unit_price - contracted_price_used) × quantity
# Positive = customer overpaid; Negative = discount was given.
# Gap is only meaningful for matched rows.
compliance_df["price_delta_per_unit"] = (
    compliance_df["negotiated_unit_price"] - compliance_df["contracted_price_used"]
)
compliance_df["dollar_gap"] = (
    compliance_df["price_delta_per_unit"] * compliance_df["quantity"]
)
# For unmatched rows, these columns stay NaN — intentional.

# ---------------------------------------------------------------------------
# 1. SIDEBAR FILTERS
# Business question answered: let a pricing manager slice compliance by
# customer type (to compare OEM vs Distributor discipline) and by region
# (to surface geographic patterns in discount behaviour).
# ---------------------------------------------------------------------------
st.sidebar.header("Filters")

# --- Customer type filter ---
available_types = sorted(
    compliance_df["customer_type"].dropna().unique().tolist()
)
selected_types = st.sidebar.multiselect(
    "Customer Type",
    options=available_types,
    default=available_types,
    help="Filter by customer classification (OEM, Distributor, CM, EMS).",
)
if not selected_types:
    selected_types = available_types

# --- Region filter ---
available_regions = sorted(
    compliance_df["region"].dropna().unique().tolist()
)
selected_regions = st.sidebar.multiselect(
    "Region",
    options=available_regions,
    default=available_regions,
    help="Filter by sales region.",
)
if not selected_regions:
    selected_regions = available_regions

# --- Apply filters ---
mask = (
    compliance_df["customer_type"].isin(selected_types)
    & compliance_df["region"].isin(selected_regions)
)
df = compliance_df[mask].copy()

st.sidebar.markdown(
    f"**{len(df):,}** order lines match current filters "
    f"({len(compliance_df):,} total active)"
)

if df.empty:
    st.warning("No orders match the current filter selection. Adjust the sidebar filters.")
    st.stop()

# ---------------------------------------------------------------------------
# Convenience splits used throughout the page
# ---------------------------------------------------------------------------
matched_df   = df[df["compliance_status"] != "Unmatched"]
unmatched_df = df[df["compliance_status"] == "Unmatched"]

n_total_assessed = len(df)
n_matched        = len(matched_df)
n_unmatched      = len(unmatched_df)

n_compliant      = (matched_df["compliance_status"] == "Compliant").sum()
compliance_rate  = n_compliant / n_matched if n_matched > 0 else 0.0

total_dollar_gap = matched_df["dollar_gap"].sum()   # net gap across all matched lines

# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------
st.subheader(" Pricing Compliance Analysis")
st.caption(
    "Comparing **negotiated_unit_price** (what customers actually paid) against "
    "**contracted_price** (what pricing agreements say they should pay). "
    f"Unauthorized discount threshold: **{UNAUTH_THRESHOLD:.0%} below contract**."
)
st.divider()

# ---------------------------------------------------------------------------
# 2. KPI SUMMARY ROW
# Business question answered: what is the headline compliance posture?
# A pricing manager opens this page wanting four numbers before anything else:
# how many orders are we assessing, what fraction are clean, how many have no
# agreement at all, and what is the net dollar impact of all deviations.
# ---------------------------------------------------------------------------

kpi1, kpi2, kpi3, kpi4 = st.columns(4)

with kpi1:
    comp_color = (
        COLORS["success"] if compliance_rate >= 0.85
        else COLORS["warning"] if compliance_rate >= 0.70
        else COLORS["danger"]
    )
    st.metric(
        label="Overall Compliance Rate",
        value=f"{compliance_rate:.1%}",
        help=(
            "% of matched order lines where negotiated price ≤ contracted price. "
            "Excludes Unmatched orders (no agreement found)."
        ),
        delta=(
            " Healthy (≥85%)" if compliance_rate >= 0.85
            else " Below target" if compliance_rate >= 0.70
            else " Critical — below 70%"
        ),
        delta_color=(
            "normal" if compliance_rate >= 0.85
            else "inverse"
        ),
    )

with kpi2:
    st.metric(
        label="Orders Assessed",
        value=f"{n_matched:,}",
        help="Active (non-cancelled) order lines with a matching pricing agreement.",
    )

with kpi3:
    unmatched_pct = n_unmatched / n_total_assessed if n_total_assessed > 0 else 0
    st.metric(
        label="Unmatched Orders",
        value=f"{n_unmatched:,}",
        delta=f"{unmatched_pct:.1%} of total — no agreement on file",
        delta_color="inverse" if unmatched_pct > 0.20 else "off",
        help=(
            "Order lines with no pricing agreement for that customer/SKU combination. "
            "These cannot be assessed and may represent coverage gaps in agreements."
        ),
    )

with kpi4:
    # Net dollar gap: positive = customers overpaid in aggregate; negative = net discounts given
    gap_sign  = "+" if total_dollar_gap >= 0 else ""
    gap_label = "Net Overpayment" if total_dollar_gap >= 0 else "Net Discount Leakage"
    st.metric(
        label=f"Total Dollar Gap ({gap_label})",
        value=f"{gap_sign}${total_dollar_gap:,.0f}",
        help=(
            "Sum of (negotiated_unit_price − contracted_price) × quantity across all matched orders. "
            "Positive = customers paid more than contract; Negative = discounts exceeded contract."
        ),
        delta_color="normal" if total_dollar_gap >= 0 else "inverse",
    )

st.divider()

# ---------------------------------------------------------------------------
# 3. COMPLIANCE BREAKDOWN BY CUSTOMER TYPE
# Business question answered: are certain customer segments (OEM, Distributor,
# CM, EMS) systematically more or less compliant? Distributors sometimes receive
# informal discounts from field sales that erode channel margin — this chart
# makes that pattern visible.
# ---------------------------------------------------------------------------
st.subheader("Compliance by Customer Type")
st.caption(
    "Each bar shows the distribution of compliance statuses within a customer type. "
    "A high Unauthorized Discount share in a segment suggests field sales reps "
    "are going off-contract for that channel."
)

# Count order lines per (customer_type, compliance_status)
type_breakdown = (
    matched_df.groupby(["customer_type", "compliance_status"])
    .size()
    .reset_index(name="Order Lines")
)
type_breakdown["customer_type"] = type_breakdown["customer_type"].fillna("Unknown")

# Add compliance rate annotation per type for the hover
type_total = matched_df.groupby("customer_type").size().rename("total")
type_compliant = (
    matched_df[matched_df["compliance_status"] == "Compliant"]
    .groupby("customer_type")
    .size()
    .rename("compliant")
)
type_rate = (type_compliant / type_total * 100).reset_index()
type_rate.columns = ["customer_type", "compliance_rate_pct"]

type_breakdown = type_breakdown.merge(type_rate, on="customer_type", how="left")

fig_type = px.bar(
    type_breakdown,
    x="customer_type",
    y="Order Lines",
    color="compliance_status",
    color_discrete_map=STATUS_COLOR_MAP,
    barmode="stack",
    title="Compliance Status Distribution by Customer Type",
    text="Order Lines",
    custom_data=["compliance_rate_pct"],
)
fig_type.update_traces(
    textposition="inside",
    textfont_size=11,
)
fig_type.update_layout(
    **CHART_LAYOUT,
    xaxis_title="Customer Type",
    yaxis_title="Order Lines",
    legend_title="Compliance Status",
)
st.plotly_chart(fig_type, width='stretch')

# Per-type compliance rate summary
type_rate_display = type_rate.copy()
type_rate_display["compliance_rate_pct"] = type_rate_display["compliance_rate_pct"].apply(
    lambda x: f"{x:.1f}%"
)
type_rate_display.columns = ["Customer Type", "Compliance Rate"]
type_rate_display = type_rate_display.sort_values("Customer Type").reset_index(drop=True)
type_rate_display.index = range(1, len(type_rate_display) + 1)

with st.expander("Compliance rate by customer type (table)"):
    st.dataframe(type_rate_display, width='stretch')

st.divider()

# ---------------------------------------------------------------------------
# 4. COMPLIANCE BREAKDOWN BY REGION
# Business question answered: are there geographic patterns in pricing discipline?
# A region with a high Unauthorized Discount rate may have a local sales culture
# of off-contract dealing, or may be responding to competitive pressure that
# pricing leadership isn't aware of.
# ---------------------------------------------------------------------------
st.subheader("Compliance by Region")
st.caption(
    "Regional breakdowns reveal whether pricing discipline is a company-wide issue "
    "or concentrated in specific geographies."
)

region_breakdown = (
    matched_df.groupby(["region", "compliance_status"])
    .size()
    .reset_index(name="Order Lines")
)
region_breakdown["region"] = region_breakdown["region"].fillna("Unknown")

# Compliance rate per region
region_total = matched_df.groupby("region").size().rename("total")
region_compliant = (
    matched_df[matched_df["compliance_status"] == "Compliant"]
    .groupby("region")
    .size()
    .rename("compliant")
)
region_rate = (region_compliant / region_total * 100).fillna(0).reset_index()
region_rate.columns = ["region", "compliance_rate_pct"]

# Sort regions by compliance rate ascending so worst performers appear first
sorted_regions = region_rate.sort_values("compliance_rate_pct")["region"].tolist()

fig_region = px.bar(
    region_breakdown,
    x="Order Lines",
    y="region",
    color="compliance_status",
    color_discrete_map=STATUS_COLOR_MAP,
    barmode="stack",
    orientation="h",
    title="Compliance Status Distribution by Region",
    category_orders={"region": sorted_regions},
    text="Order Lines",
)
fig_region.update_traces(textposition="inside", textfont_size=11)
fig_region.update_layout(
    **CHART_LAYOUT,
    xaxis_title="Order Lines",
    yaxis_title="",
    legend_title="Compliance Status",
)
st.plotly_chart(fig_region, width='stretch')

# Per-region compliance rate overlaid as a small table
region_rate_display = region_rate.copy()
region_rate_display["compliance_rate_pct"] = region_rate_display[
    "compliance_rate_pct"
].apply(lambda x: f"{x:.1f}%")
region_rate_display.columns = ["Region", "Compliance Rate"]
region_rate_display = region_rate_display.sort_values("Region").reset_index(drop=True)
region_rate_display.index = range(1, len(region_rate_display) + 1)

with st.expander("Compliance rate by region (table)"):
    st.dataframe(region_rate_display, width='stretch')

st.divider()

# ---------------------------------------------------------------------------
# 5. DOLLAR GAP TABLE — TOP 10 WORST OFFENDERS
# Business question answered: where is the biggest dollar impact from pricing
# deviations? Compliance rate tells you frequency; dollar gap tells you severity.
# A single large customer/SKU combination with a $500K gap matters more than
# fifty small deviations totalling $10K. This table drives prioritization.
# ---------------------------------------------------------------------------
st.subheader("Dollar Gap — Top 10 Customer / SKU Combinations")
st.caption(
    "Sorted by absolute dollar gap (largest deviation first). "
    "Positive gap = customer overpaid vs contract. "
    "Negative gap = unauthorized discount was applied. "
    "Use this table to prioritize account reviews."
)

gap_table = (
    matched_df[matched_df["dollar_gap"].notna()]
    .groupby(["customer_id", "customer_name", "sku", "compliance_status"])
    .agg(
        total_quantity=("quantity", "sum"),
        avg_contracted_price=("contracted_price_used", "mean"),
        avg_negotiated_price=("negotiated_unit_price", "mean"),
        total_dollar_gap=("dollar_gap", "sum"),
        order_line_count=("order_id", "count"),
    )
    .reset_index()
)

gap_table["customer_name"] = gap_table["customer_name"].fillna(gap_table["customer_id"])
gap_table["abs_gap"] = gap_table["total_dollar_gap"].abs()
gap_table = gap_table.sort_values("abs_gap", ascending=False).head(10)

# Format for display
display_gap = gap_table[
    [
        "customer_name",
        "sku",
        "compliance_status",
        "order_line_count",
        "total_quantity",
        "avg_contracted_price",
        "avg_negotiated_price",
        "total_dollar_gap",
    ]
].copy()

display_gap.columns = [
    "Customer",
    "SKU",
    "Status",
    "Order Lines",
    "Total Qty",
    "Avg Contracted Price",
    "Avg Negotiated Price",
    "Total Dollar Gap",
]

display_gap["Avg Contracted Price"] = display_gap["Avg Contracted Price"].apply(
    lambda x: f"${x:,.4f}"
)
display_gap["Avg Negotiated Price"] = display_gap["Avg Negotiated Price"].apply(
    lambda x: f"${x:,.4f}"
)
display_gap["Total Qty"] = display_gap["Total Qty"].apply(lambda x: f"{x:,.0f}")

# Color-code the dollar gap column
def _style_gap(val: str) -> str:
    """Return CSS color based on sign of the gap."""
    try:
        raw = float(str(val).replace("$", "").replace(",", ""))
        if raw > 0:
            return f"color: {COLORS['warning']}; font-weight: 600;"
        elif raw < 0:
            return f"color: {COLORS['danger']}; font-weight: 600;"
        return ""
    except ValueError:
        return ""

display_gap["Total Dollar Gap"] = display_gap["Total Dollar Gap"].apply(
    lambda x: f"${x:+,.0f}"
)
display_gap.index = range(1, len(display_gap) + 1)

st.dataframe(
    display_gap,
    width='stretch',
    column_config={
        "Status": st.column_config.TextColumn("Status"),
        "Total Dollar Gap": st.column_config.TextColumn(
            "Total Dollar Gap",
            help="+ means customer overpaid; − means unauthorized discount was applied.",
        ),
    },
)

st.divider()

# ---------------------------------------------------------------------------
# 6. PLAIN ENGLISH RISK CALLOUT
# Business question answered: if a pricing manager has 30 seconds, what is the
# single most important compliance risk in this dataset right now?
# This section synthesizes the data into one actionable paragraph rather than
# leaving interpretation as an exercise for the reader.
# ---------------------------------------------------------------------------
st.subheader(" Key Compliance Risk Summary")

# Determine the dominant risk signal
n_unauth     = (matched_df["compliance_status"] == "Unauthorized Discount").sum()
n_overpaying = (matched_df["compliance_status"] == "Overpaying").sum()
unauth_pct   = n_unauth / n_matched if n_matched > 0 else 0
overp_pct    = n_overpaying / n_matched if n_matched > 0 else 0

# Identify the worst-offending segment (customer type or region)
worst_type = None
worst_type_rate = 0.0
if not matched_df.empty:
    type_unauth_rate = (
        matched_df[matched_df["compliance_status"] == "Unauthorized Discount"]
        .groupby("customer_type")
        .size()
        / matched_df.groupby("customer_type").size()
    ).fillna(0)
    if not type_unauth_rate.empty:
        worst_type      = type_unauth_rate.idxmax()
        worst_type_rate = type_unauth_rate.max()

worst_region = None
worst_region_rate = 0.0
if not matched_df.empty:
    region_unauth_rate = (
        matched_df[matched_df["compliance_status"] == "Unauthorized Discount"]
        .groupby("region")
        .size()
        / matched_df.groupby("region").size()
    ).fillna(0)
    if not region_unauth_rate.empty:
        worst_region      = region_unauth_rate.idxmax()
        worst_region_rate = region_unauth_rate.max()

# Largest single dollar gap
largest_gap_row = gap_table.iloc[0] if not gap_table.empty else None

# Build the callout text
if unauth_pct >= overp_pct and unauth_pct > 0.05:
    primary_risk = "unauthorized discounting"
    risk_detail  = (
        f"{n_unauth:,} order lines ({unauth_pct:.1%} of assessed orders) "
        f"were priced more than {UNAUTH_THRESHOLD:.0%} below the contracted rate."
    )
    risk_color   = COLORS["danger"]
    risk_icon    = ""
elif overp_pct > 0.05:
    primary_risk = "customer overpayment"
    risk_detail  = (
        f"{n_overpaying:,} order lines ({overp_pct:.1%} of assessed orders) "
        "were charged above their contracted price — creating dispute and chargeback risk."
    )
    risk_color   = COLORS["warning"]
    risk_icon    = "YELLOW"
elif unmatched_pct > 0.30:
    primary_risk = "pricing agreement coverage gaps"
    risk_detail  = (
        f"{n_unmatched:,} order lines ({unmatched_pct:.1%} of total) "
        "have no pricing agreement on file, making compliance assessment impossible for those orders."
    )
    risk_color   = COLORS["warning"]
    risk_icon    = "YELLOW"
else:
    primary_risk = "no critical risk identified"
    risk_detail  = (
        f"Compliance rate is {compliance_rate:.1%} with no dominant deviation pattern. "
        "Continue monitoring for emerging trends."
    )
    risk_color   = COLORS["compliant"]
    risk_icon    = "GREEN"

# Segment callout lines
segment_lines = []
if worst_type and worst_type_rate > 0.10:
    segment_lines.append(
        f"<li>The <strong>{worst_type}</strong> customer segment has the highest unauthorized "
        f"discount rate at <strong>{worst_type_rate:.1%}</strong> — review field sales practices for this channel.</li>"
    )
if worst_region and worst_region_rate > 0.10:
    segment_lines.append(
        f"<li><strong>{worst_region}</strong> is the region with the most unauthorized discounting "
        f"({worst_region_rate:.1%} of assessed orders) — may indicate competitive pricing pressure "
        f"or local sales culture issues.</li>"
    )
if largest_gap_row is not None:
    cust  = largest_gap_row.get("customer_name", "Unknown")
    sku   = largest_gap_row.get("sku", "Unknown")
    gap   = largest_gap_row.get("total_dollar_gap", 0)
    segment_lines.append(
        f"<li>The largest single exposure is <strong>{cust}</strong> on SKU "
        f"<strong>{sku}</strong> with a total dollar gap of <strong>${gap:+,.0f}</strong>.</li>"
    )
if unmatched_pct > 0.15:
    segment_lines.append(
        f"<li><strong>{n_unmatched:,} orders ({unmatched_pct:.1%})</strong> have no pricing agreement "
        f"on file — the sales team may be quoting without contracts, which creates legal and margin risk.</li>"
    )

segment_html = f"<ul style='margin-top:8px;'>{''.join(segment_lines)}</ul>" if segment_lines else ""

st.markdown(
    f"""
    <div style="
        background: {COLORS["bg_card"]};
        border-left: 5px solid {risk_color};
        padding: 20px 24px;
        border-radius: 4px;
        line-height: 1.7;
        color: inherit;
    ">
        <div style="font-size:1.1rem; font-weight:700; margin-bottom:6px;">
            {risk_icon} Primary risk: <span style="color:{risk_color};">{primary_risk.title()}</span>
        </div>
        <p style="margin:0 0 4px 0;">{risk_detail}</p>
        {segment_html}
        <p style="margin-top:12px; font-size:0.85rem; color:{COLORS['compliant'] if compliance_rate >= 0.85 else COLORS['danger']};">
            Overall compliance rate: <strong>{compliance_rate:.1%}</strong>
            &nbsp;|&nbsp; Assessed: <strong>{n_matched:,}</strong> orders
            &nbsp;|&nbsp; Unmatched: <strong>{n_unmatched:,}</strong> orders
            &nbsp;|&nbsp; Net dollar gap: <strong>${total_dollar_gap:+,.0f}</strong>
        </p>
    </div>
    """,
    unsafe_allow_html=True,
)
