# ChipMetrics — Pricing Intelligence Platform

A 4-page Streamlit app for semiconductor pricing compliance and order analytics, built on messy enterprise CSV data.

**Live app:** [chipmetrics.streamlit.app](https://chipmetrics.streamlit.app)

## What it does

Four pages:
- **Order Analytics** — revenue, volume, AOV, cancellation rate, broken down by region/product/customer type
- **Pricing Compliance** — flags orders outside negotiated pricing agreements, compliance rates by type and region
- **Data Quality Report** — every cleaning decision the pipeline made, logged and visible
- **Target vs. Actual** — regional performance against growth targets

## The data problem

The source CSVs (orders, pricing agreements, product catalog, regional targets) have the kind of issues real exports have: unparseable dates, line totals that don't reconcile with quantity × price, pricing agreements in non-USD currencies, order SKUs missing from the catalog. The pipeline catches and logs all of this instead of dropping rows or computing against bad data, and the Data Quality page shows exactly what got flagged and why.

## Stack

Streamlit, pandas/NumPy for the cleaning pipeline, Plotly for charts.

## Run it locally

```bash
git clone https://github.com/Arsalan-ra/chipmetrics-pricing-platform.git
cd chipmetrics-pricing-platform
pip install -r requirements.txt
streamlit run app.py
```

## Background

Started as a take-home for a Microchip Technology BI internship application, expanded and deployed from there.
