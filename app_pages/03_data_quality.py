"""
app_pages/03_data_quality.py
------------------------------
Data Quality Report — Bonus Page for ChipMetrics.

Renders the issues_log accumulated during data loading. No raw CSV reads.
No charts — one clean table plus a total impact row and a cleaning approach
summary so reviewers understand what was done and why.
"""

import pandas as pd
import streamlit as st
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from src.data_loader import load_all_data

st.set_page_config(
    page_title="ChipMetrics — Data Quality Report",
    page_icon="",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Load data — issues_log is the only thing this page needs
# ---------------------------------------------------------------------------
_dfs, issues_log = load_all_data()

# ---------------------------------------------------------------------------
# Page header + cleaning approach summary
# ---------------------------------------------------------------------------
st.subheader(" Data Quality Report")
st.markdown(
    """
    This page documents every data quality issue identified during the loading
    pipeline and the action taken for each one.

    **Cleaning approach summary:**
    - All ingestion happens in `src/data_loader.py`. Page files receive only
      clean DataFrames — they never touch raw CSVs.
    - Issues are logged with a warning (not an exception) so the app never
      crashes on bad data. Every cleaning decision is recorded here.
    - The guiding principle: **fix what can be fixed deterministically**
      (date formats, casing, whitespace), **flag what requires judgment**
      (anomalies, orphans, currency mismatches), and **exclude from specific
      analyses** only what would produce misleading results (e.g. non-USD
      contracted prices in a USD compliance comparison).
    - No rows are silently dropped. Every removal is logged with a count.
    """
)
st.divider()

# ---------------------------------------------------------------------------
# Build and display the issues table
# ---------------------------------------------------------------------------
if not issues_log:
    st.success(
        "No data quality issues were detected. "
        "Either the data is clean or the source files could not be loaded."
    )
    st.stop()

issues_df = pd.DataFrame(issues_log)

# Normalise column names to display-friendly form
DISPLAY_COLS = {
    "file":         "File",
    "issue_type":   "Issue Type",
    "count":        "Count",
    "action_taken": "Action Taken",
    "description":  "Description",
}
issues_df = issues_df.rename(columns=DISPLAY_COLS)

# Keep only columns that exist (description is optional in older log entries)
display_cols = [c for c in DISPLAY_COLS.values() if c in issues_df.columns]
display_df = issues_df[display_cols].copy()

# ---------------------------------------------------------------------------
# Total impact row
# ---------------------------------------------------------------------------
total_count = display_df["Count"].sum()

total_row = {col: "" for col in display_cols}
total_row["File"]         = "ALL FILES"
total_row["Issue Type"]   = "TOTAL IMPACT"
total_row["Count"]        = total_count
total_row["Action Taken"] = "—"
if "Description" in display_cols:
    total_row["Description"] = f"{total_count:,} rows affected across all detected issues."

total_df  = pd.DataFrame([total_row])
final_df  = pd.concat([display_df, total_df], ignore_index=True)

# ---------------------------------------------------------------------------
# Render table with column config for readability
# ---------------------------------------------------------------------------
st.subheader(f"Issues Detected — {len(display_df)} events across all source files")

col_config = {
    "File":         st.column_config.TextColumn("File", width="medium"),
    "Issue Type":   st.column_config.TextColumn("Issue Type", width="small"),
    "Count":        st.column_config.NumberColumn("Count", format="%d", width="small"),
    "Action Taken": st.column_config.TextColumn("Action Taken", width="medium"),
}
if "Description" in display_cols:
    col_config["Description"] = st.column_config.TextColumn("Description", width="large")

st.dataframe(final_df, width='stretch', column_config=col_config)

# ---------------------------------------------------------------------------
# Issue type legend
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Issue Type Reference")

legend = {
    "DUPLICATE":          "Rows that appeared more than once on a primary key. Kept first occurrence.",
    "FORMAT":             "Values in an unexpected format (dates, casing, non-numeric strings). Normalised in place.",
    "ANOMALY":            "Values that are technically valid but business-illogical (e.g. cost > price, negative qty). Flagged and retained.",
    "ORPHAN":             "Foreign key references with no matching row in the related table. Flagged; excluded from joined analyses.",
    "MISMATCH":           "Derived value doesn't match stored value (e.g. qty × price ≠ total). Recomputed from components.",
    "UNMAPPED_VALUE":     "A value could not be mapped to the canonical set (e.g. unknown region name). Set to NaN.",
    "MISSING_FILE":       "A source CSV was not found at the expected path. All analyses depending on it are unavailable.",
    "EXCLUDED_FROM_ANALYSIS": "Rows retained in the DataFrame but excluded from a specific calculation (e.g. non-USD agreements).",
}

for issue_type, explanation in legend.items():
    st.markdown(f"**`{issue_type}`** — {explanation}")
