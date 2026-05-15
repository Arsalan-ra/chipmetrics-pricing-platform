"""
pages/01_order_analytics.py
---------------------------
Order Analytics Dashboard — Page 1 of ChipMetrics.

All data comes from data_loader.load_all_data(). No raw CSV reads here.
Charts use Plotly. Sidebar filters (date range + region) propagate to every
chart and KPI on the page.

Page structure:
    1. Sidebar filters
    2. KPI summary row
    3. Charts (revenue breakdown, order status, top customers)
    4. Business insight sections (concentration, cancellation, AOV by type)
"""

import pandas as pd
import numpy as np
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup — allows running this page standalone or via app.py
# ---------------------------------------------------------------------------
sys.path.append(str(Path(__file__).parent.parent))
from src.data_loader import load_all_data

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="ChipMetrics — Order Analytics",
    page_icon="",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Color palette — consistent across all charts
# ---------------------------------------------------------------------------
COLORS = {
    "primary":    "#0F62FE",   # IBM Blue — signals enterprise
    "accent":     "#FF6B35",   # Orange — for highlights and callouts
    "success":    "#24A148",   # Green — good compliance, on-target
    "warning":    "#F1C21B",   # Yellow — caution signals
    "danger":     "#DA1E28",   # Red — violations, over-cancel flags
    "neutral":    "#6F6F6F",   # Mid-grey — secondary text
    "bg_card":    "rgba(255,255,255,0.08)",   # Light card background
}

PLOTLY_PALETTE = [
    "#0F62FE", "#FF6B35", "#24A148", "#8A3FFC",
    "#F1C21B", "#1192E8", "#009D9A", "#FA4D56",
]

CHART_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="IBM Plex Sans, sans-serif", color="#161616"),
    margin=dict(l=24, r=24, t=40, b=24),
)

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
dfs, issues_log = load_all_data()
orders_raw  = dfs.get("orders")
catalog     = dfs.get("catalog")

if orders_raw is None:
    st.error("customer_orders.csv could not be loaded. Check your data/ folder.")
    st.stop()

# ---------------------------------------------------------------------------
# Enrich orders with product_family from catalog (needed for breakdown chart)
# ---------------------------------------------------------------------------
if catalog is not None:
    orders_enriched = orders_raw.merge(
        catalog[["sku", "product_family"]],
        on="sku",
        how="left",
    )
else:
    orders_enriched = orders_raw.copy()
    orders_enriched["product_family"] = "Unknown"

# ---------------------------------------------------------------------------
# 1. SIDEBAR FILTERS
# Business question answered: let the user slice every metric by time window
# and geography so regional sales VPs can interrogate their own numbers.
# ---------------------------------------------------------------------------
st.sidebar.header("Filters")

# --- Date range filter ---
min_date = orders_enriched["order_date"].min()
max_date = orders_enriched["order_date"].max()

if pd.isna(min_date) or pd.isna(max_date):
    min_date = pd.Timestamp("2024-01-01")
    max_date = pd.Timestamp("2025-12-31")

date_range = st.sidebar.date_input(
    "Order Date Range",
    value=(min_date.date(), max_date.date()),
    min_value=min_date.date(),
    max_value=max_date.date(),
)

# Gracefully handle the case where the user has only selected one date
if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
    start_date, end_date = pd.Timestamp(date_range[0]), pd.Timestamp(date_range[1])
else:
    start_date, end_date = min_date, max_date

# --- Region multiselect filter ---
available_regions = sorted(
    orders_enriched["region"].dropna().unique().tolist()
)
selected_regions = st.sidebar.multiselect(
    "Region",
    options=available_regions,
    default=available_regions,
    help="Select one or more regions. All selected by default.",
)

# Fall back to all regions if user clears selection (prevents empty dashboard)
if not selected_regions:
    selected_regions = available_regions

# --- Apply filters ---
mask = (
    (orders_enriched["order_date"] >= start_date)
    & (orders_enriched["order_date"] <= end_date)
    & (orders_enriched["region"].isin(selected_regions))
)
df = orders_enriched[mask].copy()

# Show how many orders remain after filtering
st.sidebar.markdown(
    f"**{len(df):,}** order lines match current filters "
    f"({len(orders_enriched):,} total)"
)

if df.empty:
    st.warning("No orders match the current filter selection. Adjust the sidebar filters.")
    st.stop()

# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------
st.title(" Order Analytics Dashboard")
st.caption(
    f"Showing orders from **{start_date.strftime('%b %d, %Y')}** "
    f"to **{end_date.strftime('%b %d, %Y')}** — "
    f"Region(s): **{', '.join(selected_regions)}**"
)
st.divider()

# ---------------------------------------------------------------------------
# 2. KPI SUMMARY ROW
# Business question answered: give a sales VP the four numbers they reach for
# first — total bookings, volume of transactions, ticket size, and attrition
# signal — without making them read a chart.
# ---------------------------------------------------------------------------

# Only count Shipped + Open as "booked" revenue; exclude Cancelled
booked_df = df[df["order_status"].str.lower() != "cancelled"]

total_revenue      = booked_df["total_line_value"].sum()
total_orders       = df["order_id"].nunique()
avg_order_value    = (
    booked_df.groupby("order_id")["total_line_value"].sum().mean()
    if not booked_df.empty else 0
)
n_cancelled        = df[df["order_status"].str.lower() == "cancelled"]["order_id"].nunique()
cancellation_rate  = n_cancelled / total_orders if total_orders > 0 else 0

kpi1, kpi2, kpi3, kpi4 = st.columns(4)

with kpi1:
    st.metric(
        label="Total Revenue (Booked)",
        value=f"${total_revenue:,.0f}",
        help="Sum of total_line_value for Shipped + Open orders in the selected period.",
    )
with kpi2:
    st.metric(
        label="Total Orders",
        value=f"{total_orders:,}",
        help="Unique order_id count across all statuses in the selected period.",
    )
with kpi3:
    st.metric(
        label="Avg Order Value",
        value=f"${avg_order_value:,.0f}",
        help="Mean of per-order total revenue (Shipped + Open only).",
    )
with kpi4:
    cancel_color = "inverse" if cancellation_rate > 0.15 else "normal"
    st.metric(
        label="Cancellation Rate",
        value=f"{cancellation_rate:.1%}",
        help="Cancelled orders ÷ total orders. Above 15% warrants investigation.",
        delta=f"{' Above 15% threshold' if cancellation_rate > 0.15 else 'Within normal range'}",
        delta_color=cancel_color,
    )

st.divider()

# ---------------------------------------------------------------------------
# 3. CHARTS
# ---------------------------------------------------------------------------

# --- 3a. Revenue Breakdown (dynamic dimension) ---
# Business question answered: which dimension is driving (or capping) revenue?
# Switching between region / product family / customer type lets a sales VP
# quickly test multiple hypotheses without opening another tool.

st.subheader("Revenue Breakdown")
breakdown_dim = st.radio(
    "Break down by",
    options=["Region", "Product Family", "Customer Type"],
    horizontal=True,
    key="breakdown_radio",
)

DIM_MAP = {
    "Region":        "region",
    "Product Family": "product_family",
    "Customer Type": "customer_type",
}
dim_col = DIM_MAP[breakdown_dim]

breakdown_df = (
    booked_df.groupby(dim_col, dropna=False)["total_line_value"]
    .sum()
    .reset_index()
    .rename(columns={dim_col: breakdown_dim, "total_line_value": "Revenue (USD)"})
    .sort_values("Revenue (USD)", ascending=False)
)
breakdown_df[breakdown_dim] = breakdown_df[breakdown_dim].fillna("Unknown")

fig_breakdown = px.bar(
    breakdown_df,
    x=breakdown_dim,
    y="Revenue (USD)",
    color=breakdown_dim,
    color_discrete_sequence=PLOTLY_PALETTE,
    text_auto=".3s",
    title=f"Revenue by {breakdown_dim}",
)
fig_breakdown.update_traces(textposition="outside", cliponaxis=False)
fig_breakdown.update_layout(
    **CHART_LAYOUT,
    showlegend=False,
    yaxis_title="Revenue (USD)",
    xaxis_title="",
)
st.plotly_chart(fig_breakdown, use_container_width=True)

st.divider()

# Split the next two charts side-by-side
col_left, col_right = st.columns(2)

# --- 3b. Order Status Distribution ---
# Business question answered: is the pipeline healthy? A rising Cancelled slice
# or a growing Open (unfulfilled) slice signals supply chain or relationship issues.

with col_left:
    st.subheader("Order Status Distribution")

    status_df = (
        df.groupby("order_status", dropna=False)["order_id"]
        .nunique()
        .reset_index()
        .rename(columns={"order_id": "Order Count", "order_status": "Status"})
    )
    status_df["Status"] = status_df["Status"].fillna("Unknown")

    # Custom color map: Cancelled = red, Shipped = green, Open = blue
    status_color_map = {
        "Cancelled":    COLORS["danger"],
        "Shipped":      COLORS["success"],
        "Open":         COLORS["primary"],
        "On Hold":      COLORS["warning"],
        "Backordered":  COLORS["accent"],
        "Unknown":      COLORS["neutral"],
    }

    fig_status = px.pie(
        status_df,
        names="Status",
        values="Order Count",
        color="Status",
        color_discrete_map=status_color_map,
        hole=0.45,
        title="Orders by Status",
    )
    fig_status.update_traces(
        textinfo="percent+label",
        textposition="outside",
        pull=[0.04] * len(status_df),
    )
    fig_status.update_layout(**CHART_LAYOUT)
    st.plotly_chart(fig_status, use_container_width=True)

# --- 3c. Top 10 Customers by Revenue ---
# Business question answered: who are we most dependent on? This chart is the
# entry point for the concentration analysis below — see if the visual Pareto
# pattern matches the numbers.

with col_right:
    st.subheader("Top 10 Customers by Revenue")

    top_customers_df = (
        booked_df.groupby(["customer_id", "customer_name"], dropna=False)["total_line_value"]
        .sum()
        .reset_index()
        .rename(columns={"total_line_value": "Revenue (USD)", "customer_name": "Customer"})
        .sort_values("Revenue (USD)", ascending=False)
        .head(10)
    )
    top_customers_df["Customer"] = top_customers_df["Customer"].fillna(
        top_customers_df["customer_id"]
    )

    fig_top_cust = px.bar(
        top_customers_df,
        x="Revenue (USD)",
        y="Customer",
        orientation="h",
        color="Revenue (USD)",
        color_continuous_scale=["#C6DEF7", COLORS["primary"]],
        text_auto=".3s",
        title="Top 10 Customers — Booked Revenue",
    )
    fig_top_cust.update_traces(textposition="outside")
    fig_top_cust.update_layout(
        **CHART_LAYOUT,
        yaxis=dict(autorange="reversed"),
        coloraxis_showscale=False,
        xaxis_title="Revenue (USD)",
        yaxis_title="",
    )
    st.plotly_chart(fig_top_cust, use_container_width=True)

st.divider()

# ---------------------------------------------------------------------------
# 4. BUSINESS INSIGHT SECTIONS
# ---------------------------------------------------------------------------

st.subheader("Business Insights")

# --- 4a. Customer Concentration ---
# Business question answered: are we too dependent on a handful of accounts?
# If the top 5 customers represent >50% of revenue, one lost deal materially
# hurts the quarter. Sales leadership needs to know this number cold.

st.markdown("####  Customer Concentration")

total_rev_booked = booked_df["total_line_value"].sum()

customer_rev = (
    booked_df.groupby(["customer_id", "customer_name"])["total_line_value"]
    .sum()
    .reset_index()
    .rename(columns={"total_line_value": "Revenue (USD)", "customer_name": "Customer"})
    .sort_values("Revenue (USD)", ascending=False)
)
customer_rev["Customer"] = customer_rev["Customer"].fillna(customer_rev["customer_id"])

top5_rev   = customer_rev.head(5)["Revenue (USD)"].sum()
top5_share = top5_rev / total_rev_booked if total_rev_booked > 0 else 0

# Running cumulative share for waterfall display
customer_rev["Cumulative Share"] = (
    customer_rev["Revenue (USD)"].cumsum() / total_rev_booked * 100
)
customer_rev["Share (%)"] = customer_rev["Revenue (USD)"] / total_rev_booked * 100

# Callout card
if top5_share >= 0.60:
    concentration_signal = " High concentration risk"
    concentration_color  = COLORS["danger"]
elif top5_share >= 0.40:
    concentration_signal = "YELLOW Moderate concentration"
    concentration_color  = COLORS["warning"]
else:
    concentration_signal = "GREEN Well-distributed revenue"
    concentration_color  = COLORS["success"]

st.markdown(
    f"""
    <div style="
        background: {COLORS["bg_card"]};
        border-left: 4px solid {concentration_color};
        padding: 16px 20px;
        border-radius: 4px;
        margin-bottom: 16px;
        color: inherit;
    ">
        <strong style="font-size:1.05rem;">{concentration_signal}</strong><br>
        The top 5 customers account for <strong>{top5_share:.1%}</strong> of booked revenue
        (${top5_rev:,.0f} of ${total_rev_booked:,.0f}).
        {"Losing a single top account would have an outsized impact on the quarter."
         if top5_share >= 0.40 else
         "Revenue is spread across many accounts, limiting single-customer risk."}
    </div>
    """,
    unsafe_allow_html=True,
)

# Show top 10 table with share column
display_conc = customer_rev.head(10)[["Customer", "Revenue (USD)", "Share (%)", "Cumulative Share"]].copy()
display_conc["Revenue (USD)"] = display_conc["Revenue (USD)"].apply(lambda x: f"${x:,.0f}")
display_conc["Share (%)"]     = display_conc["Share (%)"].apply(lambda x: f"{x:.1f}%")
display_conc["Cumulative Share"] = display_conc["Cumulative Share"].apply(lambda x: f"{x:.1f}%")
display_conc.index = range(1, len(display_conc) + 1)
st.dataframe(display_conc, use_container_width=True)

st.divider()

# --- 4b. Cancellation Rate by Customer ---
# Business question answered: which customers are consistently cancelling?
# A customer with >20% cancellation rate either has demand planning problems
# or is using orders as forecast placeholders — both are flags for the account team.

st.markdown("####  Cancellation Rate by Customer")
st.caption("Customers with a cancellation rate above 20% are flagged — they may be over-ordering or signaling churn risk.")

cust_cancel = (
    df.groupby(["customer_id", "customer_name"])
    .agg(
        total_orders=("order_id", "nunique"),
        cancelled_orders=("order_id", lambda x: df.loc[x.index][
            df.loc[x.index, "order_status"].str.lower() == "cancelled"
        ]["order_id"].nunique()),
    )
    .reset_index()
)

# Recalculate cleanly to avoid the groupby-lambda complexity
order_totals = df.groupby("customer_id")["order_id"].nunique().rename("total_orders")
cancelled_totals = (
    df[df["order_status"].str.lower() == "cancelled"]
    .groupby("customer_id")["order_id"]
    .nunique()
    .rename("cancelled_orders")
)

cust_cancel = (
    pd.concat([order_totals, cancelled_totals], axis=1)
    .fillna(0)
    .reset_index()
)
cust_cancel["cancelled_orders"] = cust_cancel["cancelled_orders"].astype(int)
cust_cancel["total_orders"]     = cust_cancel["total_orders"].astype(int)
cust_cancel["cancellation_rate"] = (
    cust_cancel["cancelled_orders"] / cust_cancel["total_orders"]
)

# Merge in customer name
name_map = (
    df[["customer_id", "customer_name"]]
    .dropna(subset=["customer_name"])
    .drop_duplicates("customer_id")
    .set_index("customer_id")["customer_name"]
)
cust_cancel["Customer"] = cust_cancel["customer_id"].map(name_map).fillna(
    cust_cancel["customer_id"]
)

# Only show customers with at least 3 orders (avoids 100% from a single order)
cust_cancel_filtered = cust_cancel[cust_cancel["total_orders"] >= 3].sort_values(
    "cancellation_rate", ascending=False
)

# Flag threshold
CANCEL_THRESHOLD = 0.20

flagged = cust_cancel_filtered[cust_cancel_filtered["cancellation_rate"] > CANCEL_THRESHOLD]
n_flagged = len(flagged)

if n_flagged > 0:
    st.markdown(
        f"<span style='color:{COLORS['danger']}; font-weight:600;'>"
        f" {n_flagged} customer(s) exceed the 20% cancellation threshold.</span>",
        unsafe_allow_html=True,
    )

fig_cancel = px.bar(
    cust_cancel_filtered.head(20),
    x="Customer",
    y="cancellation_rate",
    color="cancellation_rate",
    color_continuous_scale=[COLORS["success"], COLORS["warning"], COLORS["danger"]],
    color_continuous_midpoint=CANCEL_THRESHOLD,
    range_color=[0, max(cust_cancel_filtered["cancellation_rate"].max(), 0.4)],
    text=cust_cancel_filtered.head(20)["cancellation_rate"].apply(lambda x: f"{x:.0%}"),
    title="Cancellation Rate by Customer (top 20, min 3 orders)",
    custom_data=["total_orders", "cancelled_orders"],
)
fig_cancel.update_traces(textposition="outside", cliponaxis=False)
fig_cancel.add_hline(
    y=CANCEL_THRESHOLD,
    line_dash="dash",
    line_color=COLORS["danger"],
    annotation_text="20% threshold",
    annotation_position="top right",
)
fig_cancel.update_layout(
    **CHART_LAYOUT,
    yaxis_tickformat=".0%",
    yaxis_title="Cancellation Rate",
    xaxis_title="",
    coloraxis_showscale=False,
)
fig_cancel.update_traces(
    hovertemplate=(
        "<b>%{x}</b><br>"
        "Cancellation Rate: %{y:.1%}<br>"
        "Total Orders: %{customdata[0]}<br>"
        "Cancelled: %{customdata[1]}<extra></extra>"
    )
)
st.plotly_chart(fig_cancel, use_container_width=True)

st.divider()

# --- 4c. Average Order Value by Customer Type ---
# Business question answered: do different customer types (OEM, Distributor, CM, EMS)
# behave differently in terms of deal size? This informs how sales resources should
# be allocated — a Distributor with low AOV but high volume is a different motion
# than an OEM writing large one-off orders.

st.markdown("####  Average Order Value by Customer Type")
st.caption(
    "OEMs typically write larger but less frequent orders. "
    "Distributors push higher volume at lower per-order value. "
    "Significant deviations from this pattern warrant a closer look."
)

# AOV = total revenue per order, then average across orders within customer type
order_rev = (
    booked_df.groupby(["order_id", "customer_type"])["total_line_value"]
    .sum()
    .reset_index()
)
aov_by_type = (
    order_rev.groupby("customer_type")["total_line_value"]
    .agg(["mean", "median", "count"])
    .reset_index()
    .rename(columns={
        "customer_type": "Customer Type",
        "mean":   "Mean AOV (USD)",
        "median": "Median AOV (USD)",
        "count":  "Order Count",
    })
    .sort_values("Mean AOV (USD)", ascending=False)
)
aov_by_type["Customer Type"] = aov_by_type["Customer Type"].fillna("Unknown")

fig_aov = go.Figure()

fig_aov.add_trace(
    go.Bar(
        name="Mean AOV",
        x=aov_by_type["Customer Type"],
        y=aov_by_type["Mean AOV (USD)"],
        marker_color=COLORS["primary"],
        text=aov_by_type["Mean AOV (USD)"].apply(lambda x: f"${x:,.0f}"),
        textposition="outside",
    )
)
fig_aov.add_trace(
    go.Bar(
        name="Median AOV",
        x=aov_by_type["Customer Type"],
        y=aov_by_type["Median AOV (USD)"],
        marker_color=COLORS["accent"],
        text=aov_by_type["Median AOV (USD)"].apply(lambda x: f"${x:,.0f}"),
        textposition="outside",
    )
)

fig_aov.update_layout(
    **CHART_LAYOUT,
    barmode="group",
    title="Mean vs. Median AOV by Customer Type",
    yaxis_title="Order Value (USD)",
    xaxis_title="",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
)

st.plotly_chart(fig_aov, use_container_width=True)

# Insight callout: flag when mean >> median (skew from a few large orders)
for _, row in aov_by_type.iterrows():
    if row["Mean AOV (USD)"] > 0 and row["Median AOV (USD)"] > 0:
        skew_ratio = row["Mean AOV (USD)"] / row["Median AOV (USD)"]
        if skew_ratio > 2.0 and row["Order Count"] >= 5:
            st.caption(
                f" **{row['Customer Type']}**: mean AOV is {skew_ratio:.1f}× the median, "
                "suggesting a small number of very large orders are pulling the average up. "
                "Median is a more reliable benchmark for typical deal size."
            )

# Summary table
aov_by_type["Mean AOV (USD)"]   = aov_by_type["Mean AOV (USD)"].apply(lambda x: f"${x:,.0f}")
aov_by_type["Median AOV (USD)"] = aov_by_type["Median AOV (USD)"].apply(lambda x: f"${x:,.0f}")
aov_by_type.index = range(1, len(aov_by_type) + 1)
st.dataframe(aov_by_type, use_container_width=True)
