# ChipMetrics — Pricing Intelligence Platform

A 4-page Streamlit analytics platform for semiconductor pricing compliance, order analysis, and regional performance tracking — built to turn messy, inconsistent enterprise order data into a usable internal BI tool.

**Live app:** [chipmetrics.streamlit.app](https://chipmetrics.streamlit.app)

## Why this exists

Enterprise order data is rarely clean. This project takes four real-world-style CSV exports (customer orders, pricing agreements, product catalog, regional targets) — with the kinds of inconsistencies that show up in actual sales/ops data: unparseable dates, mismatched currencies, line-item totals that don't reconcile, SKUs missing from the catalog — and builds a pipeline that cleans, flags, and logs every correction transparently, rather than silently dropping or guessing at bad rows.

## What it does

- **Order Analytics** — revenue, order volume, average order value, and cancellation rate, with breakdowns by region, product family, and customer type
- **Pricing Compliance** — flags orders that fall outside negotiated pricing agreements, with compliance rates by product type and region
- **Data Quality Report** — full transparency into every cleaning decision the pipeline made (e.g., "508 values in order_date could not be parsed and were set to NaT"), so nothing is silently altered
- **Target vs. Actual** — regional performance against growth targets, with attainment tracking

## How the data pipeline works

All four source CSVs are loaded once per session through a caching layer (`@st.cache_data`) and run through a validation/cleaning pipeline that:
- Parses and flags malformed dates rather than dropping rows
- Detects line-item totals that don't reconcile (`quantity × price ≠ total`)
- Identifies and excludes non-USD pricing agreements from compliance calculations rather than miscalculating against them
- Cross-references order SKUs against the product catalog and flags unmatched ones

Every cleaning action is logged and surfaced in the Data Quality Report page — the goal was to make the cleaning process auditable, not a black box.

## Tech stack

- **Streamlit** — UI and app framework
- **pandas / NumPy** — data cleaning, validation, transformation
- **Plotly** — interactive charts

## Run it locally

```bash
git clone https://github.com/Arsalan-ra/chipmetrics-pricing-platform.git
cd chipmetrics-pricing-platform
pip install -r requirements.txt
streamlit run app.py
```

## Background

Originally built as a take-home exercise for a Microchip Technology BI internship application, then expanded and deployed as a standalone project.
