"""
src/data_loader.py
------------------
Single entry point: load_all_data() -> (dataframes: dict, issues_log: list[dict])

Design rules:
  - All cleaning happens here. Pages import clean dataframes only.
  - Warnings are used instead of exceptions so bad data never crashes the app.
  - issues_log accumulates one dict per cleaning event and is returned to the
    caller so pages (and the optional Data Quality page) can render it directly.
  - @st.cache_data is applied so the full pipeline runs once per session.
"""

import warnings
import pandas as pd
import numpy as np
import streamlit as st
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DATA_DIR = Path(__file__).parent.parent / "data"

# Canonical region map — catches every naming variant seen in semiconductor ERP/CRM exports.
# Without this, region-level joins and charts silently split one market into multiple rows.
REGION_NORMALIZATION_MAP = {
    # Americas variants
    "americas":         "Americas",
    "amer":             "Americas",
    "am":               "Americas",
    "north america":    "Americas",
    "na":               "Americas",
    "latam":            "Americas",   # flag separately if needed, but map to Americas for now
    # EMEA variants
    "emea":             "EMEA",
    "europe":           "EMEA",
    "europe middle east africa": "EMEA",
    "eur":              "EMEA",
    "eu":               "EMEA",
    # APAC variants
    "apac":             "APAC",
    "asia pacific":     "APAC",
    "asia-pacific":     "APAC",
    "ap":               "APAC",
    "asia":             "APAC",
    # Japan — sometimes broken out separately in semiconductor sales data
    "japan":            "Japan",
    "jpn":              "Japan",
    "jp":               "Japan",
    # Greater China — also sometimes a separate region in ERP systems
    "greater china":    "Greater China",
    "china":            "Greater China",
    "chn":              "Greater China",
}

# Canonical lifecycle status values drawn from industry standard (JEDEC / distributor practice)
LIFECYCLE_NORMALIZATION_MAP = {
    "active":           "Active",
    "eol":              "EOL",
    "end of life":      "EOL",
    "end-of-life":      "EOL",
    "nrnd":             "NRND",
    "not recommended for new designs": "NRND",
    "discontinued":     "Discontinued",
    "disc":             "Discontinued",
    "preview":          "Preview",
    "in development":   "Preview",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _append_issue(
    issues_log: list,
    file: str,
    issue_type: str,
    count: int,
    action_taken: str,
    description: str = "",
) -> None:
    """Append one data quality event to the running issues log."""
    issues_log.append(
        {
            "file":         file,
            "issue_type":   issue_type,
            "count":        count,
            "action_taken": action_taken,
            "description":  description,
        }
    )


def _safe_read_csv(path: Path, issues_log: list) -> pd.DataFrame | None:
    """
    Read a CSV and return a DataFrame, or None if the file is missing.
    Missing files produce a warning rather than an exception so the app
    can still render partial data.
    """
    if not path.exists():
        warnings.warn(f"Data file not found: {path}", UserWarning)
        _append_issue(
            issues_log,
            file=path.name,
            issue_type="MISSING_FILE",
            count=0,
            action_taken="SKIPPED",
            description=f"File not found at {path}. All analyses depending on this file will be unavailable.",
        )
        return None
    return pd.read_csv(path, dtype=str)   # read everything as str; we cast explicitly below


def _normalize_region_column(df: pd.DataFrame, col: str, file: str, issues_log: list) -> pd.DataFrame:
    """
    Normalize a region column in-place using REGION_NORMALIZATION_MAP.

    Business problem prevented: without normalization, 'AMER' and 'Americas'
    aggregate as two separate rows in every region chart and join, silently
    undercounting revenue for that market.
    """
    original = df[col].copy()
    df[col] = df[col].str.strip().str.lower().map(REGION_NORMALIZATION_MAP)

    unmapped_mask = df[col].isna() & original.notna()
    if unmapped_mask.any():
        unmapped_vals = original[unmapped_mask].unique().tolist()
        warnings.warn(
            f"[{file}] {unmapped_mask.sum()} rows have unrecognized region values: {unmapped_vals}. "
            "They will appear as NaN in region analyses.",
            UserWarning,
        )
        _append_issue(
            issues_log,
            file=file,
            issue_type="UNMAPPED_VALUE",
            count=int(unmapped_mask.sum()),
            action_taken="FLAGGED_AS_NULL",
            description=f"Unrecognized region values in column '{col}': {unmapped_vals}",
        )

    changed = (df[col] != original) & ~unmapped_mask
    if changed.any():
        _append_issue(
            issues_log,
            file=file,
            issue_type="FORMAT",
            count=int(changed.sum()),
            action_taken="CORRECTED",
            description=f"Region values in '{col}' normalized to canonical names.",
        )

    return df


def _parse_dates(df: pd.DataFrame, cols: list[str], file: str, issues_log: list) -> pd.DataFrame:
    """
    Parse date columns to datetime, tolerating mixed formats.

    Business problem prevented: date columns stored as raw strings will fail
    silently on comparison operators (e.g. ship_date < order_date), and
    fiscal quarter derivation from order_date will be impossible.
    """
    for col in cols:
        if col not in df.columns:
            continue
        original_nulls = df[col].isna().sum()
        df[col] = pd.to_datetime(df[col], errors="coerce")
        new_nulls = df[col].isna().sum()
        newly_coerced = new_nulls - original_nulls
        if newly_coerced > 0:
            warnings.warn(
                f"[{file}] {newly_coerced} values in '{col}' could not be parsed as dates and were set to NaT.",
                UserWarning,
            )
            _append_issue(
                issues_log,
                file=file,
                issue_type="FORMAT",
                count=int(newly_coerced),
                action_taken="COERCED_TO_NAT",
                description=f"Unparseable date strings in '{col}' converted to NaT.",
            )
    return df


def _cast_numeric(
    df: pd.DataFrame,
    cols: list[str],
    file: str,
    issues_log: list,
    allow_negative: bool = False,
) -> pd.DataFrame:
    """
    Cast columns to float, coercing non-numeric strings to NaN.
    Optionally flags negative values where they are nonsensical (prices, quantities).

    Business problem prevented: numeric columns read as strings will cause
    aggregations (revenue sums, compliance deltas) to silently concatenate
    strings instead of summing numbers.
    """
    for col in cols:
        if col not in df.columns:
            continue
        original_nulls = df[col].isna().sum()
        df[col] = pd.to_numeric(df[col], errors="coerce")
        new_nulls = df[col].isna().sum()
        newly_coerced = new_nulls - original_nulls
        if newly_coerced > 0:
            warnings.warn(
                f"[{file}] {newly_coerced} non-numeric values in '{col}' converted to NaN.",
                UserWarning,
            )
            _append_issue(
                issues_log,
                file=file,
                issue_type="FORMAT",
                count=int(newly_coerced),
                action_taken="COERCED_TO_NAN",
                description=f"Non-numeric strings in '{col}' could not be cast to float.",
            )

        if not allow_negative:
            neg_mask = df[col] < 0
            if neg_mask.any():
                warnings.warn(
                    f"[{file}] {neg_mask.sum()} negative values found in '{col}'. Flagged but retained.",
                    UserWarning,
                )
                _append_issue(
                    issues_log,
                    file=file,
                    issue_type="ANOMALY",
                    count=int(neg_mask.sum()),
                    action_taken="FLAGGED",
                    description=f"Negative values in '{col}' — may indicate credit memos or data entry errors.",
                )
    return df


# ---------------------------------------------------------------------------
# Per-file loaders
# ---------------------------------------------------------------------------

def _load_catalog(issues_log: list) -> pd.DataFrame | None:
    """
    Load and clean product_catalog.csv.

    Columns: sku, product_family, series_name, package_type, pin_count,
             flash_kb, unit_cost_usd, list_price_usd, lifecycle_status,
             launch_date, lead_time_weeks
    """
    FILE = "product_catalog.csv"
    df = _safe_read_csv(DATA_DIR / FILE, issues_log)
    if df is None:
        return None

    # --- Strip whitespace from all string columns ---
    # Prevents 'MCU-001 ' and 'MCU-001' from being treated as different SKUs,
    # which would cause silent join failures downstream.
    df = df.apply(lambda col: col.str.strip() if col.dtype == "object" else col)

    # --- Normalize SKU to uppercase ---
    # SKU is the primary join key across all four files. Mixed casing would
    # cause joins to miss matches and undercount orders per product.
    if "sku" in df.columns:
        df["sku"] = df["sku"].str.upper()

    # --- Deduplicate on SKU ---
    # Duplicate SKUs in the catalog would fan out order rows on join, inflating
    # revenue figures. Keep first occurrence; log the count removed.
    before = len(df)
    df = df.drop_duplicates(subset=["sku"], keep="first")
    dupes = before - len(df)
    if dupes > 0:
        _append_issue(
            issues_log, FILE, "DUPLICATE", dupes, "DROPPED",
            "Duplicate SKU rows in product_catalog — keeping first occurrence.",
        )

    # --- Parse launch_date ---
    df = _parse_dates(df, ["launch_date"], FILE, issues_log)

    # --- Cast numeric columns ---
    df = _cast_numeric(
        df,
        ["unit_cost_usd", "list_price_usd", "pin_count", "flash_kb", "lead_time_weeks"],
        FILE, issues_log, allow_negative=False,
    )

    # --- Flag rows where unit cost exceeds list price ---
    # Cost > list price means the company is selling below cost. These rows are
    # retained but flagged so margin analyses don't silently report negative margin
    # without a business explanation.
    if {"unit_cost_usd", "list_price_usd"}.issubset(df.columns):
        invalid_margin = df["unit_cost_usd"] > df["list_price_usd"]
        invalid_margin = invalid_margin & df["unit_cost_usd"].notna() & df["list_price_usd"].notna()
        if invalid_margin.any():
            warnings.warn(
                f"[{FILE}] {invalid_margin.sum()} SKUs have unit_cost_usd > list_price_usd.",
                UserWarning,
            )
            _append_issue(
                issues_log, FILE, "ANOMALY", int(invalid_margin.sum()), "FLAGGED",
                "unit_cost_usd exceeds list_price_usd — implies negative gross margin.",
            )
        # Flag zero list prices — they will cause division-by-zero in discount % calculations
        zero_price = df["list_price_usd"] == 0
        if zero_price.any():
            _append_issue(
                issues_log, FILE, "ANOMALY", int(zero_price.sum()), "FLAGGED",
                "list_price_usd is zero — excluded from discount percentage calculations.",
            )

    # --- Normalize lifecycle_status ---
    # Inconsistent casing/phrasing (e.g. "EOL" vs "End of Life") would create
    # phantom status categories in charts and make lifecycle filters unreliable.
    if "lifecycle_status" in df.columns:
        original = df["lifecycle_status"].copy()
        df["lifecycle_status"] = (
            df["lifecycle_status"]
            .str.strip()
            .str.lower()
            .map(LIFECYCLE_NORMALIZATION_MAP)
        )
        unmapped = df["lifecycle_status"].isna() & original.notna()
        if unmapped.any():
            _append_issue(
                issues_log, FILE, "UNMAPPED_VALUE", int(unmapped.sum()), "FLAGGED_AS_NULL",
                f"Unrecognized lifecycle_status values: {original[unmapped].unique().tolist()}",
            )

    return df


def _load_orders(issues_log: list) -> pd.DataFrame | None:
    """
    Load and clean customer_orders.csv.

    Columns: order_id, order_line, order_date, ship_date, customer_id,
             customer_name, customer_type, region, country, sku, quantity,
             negotiated_unit_price, total_line_value, order_status, priority
    """
    FILE = "customer_orders.csv"
    df = _safe_read_csv(DATA_DIR / FILE, issues_log)
    if df is None:
        return None

    # --- Strip whitespace from all string columns ---
    df = df.apply(lambda col: col.str.strip() if col.dtype == "object" else col)

    # --- Normalize SKU to uppercase ---
    # Must match the same transformation applied to product_catalog.sku.
    if "sku" in df.columns:
        df["sku"] = df["sku"].str.upper()

    # --- Normalize customer_id ---
    # customer_id is a join key with pricing_agreements. Casing inconsistencies
    # would cause agreements to be silently missed during compliance checks.
    if "customer_id" in df.columns:
        df["customer_id"] = df["customer_id"].str.upper().str.strip()

    # --- Deduplicate on (order_id, order_line) ---
    # Duplicate order lines would double-count revenue in every aggregation.
    if {"order_id", "order_line"}.issubset(df.columns):
        before = len(df)
        df = df.drop_duplicates(subset=["order_id", "order_line"], keep="first")
        dupes = before - len(df)
        if dupes > 0:
            _append_issue(
                issues_log, FILE, "DUPLICATE", dupes, "DROPPED",
                "Duplicate (order_id, order_line) pairs — keeping first occurrence.",
            )

    # --- Parse date columns ---
    df = _parse_dates(df, ["order_date", "ship_date"], FILE, issues_log)

    # --- Flag logical date inversions: ship_date before order_date ---
    # An order that ships before it was placed is a data entry error. Revenue
    # recognition and lead-time analyses would be distorted.
    if {"order_date", "ship_date"}.issubset(df.columns):
        inversion = (
            df["ship_date"].notna()
            & df["order_date"].notna()
            & (df["ship_date"] < df["order_date"])
        )
        if inversion.any():
            warnings.warn(
                f"[{FILE}] {inversion.sum()} rows have ship_date before order_date.",
                UserWarning,
            )
            _append_issue(
                issues_log, FILE, "ANOMALY", int(inversion.sum()), "FLAGGED",
                "ship_date is earlier than order_date — likely data entry errors.",
            )

    # --- Cast numeric columns ---
    df = _cast_numeric(
        df,
        ["quantity", "negotiated_unit_price", "total_line_value"],
        FILE, issues_log, allow_negative=False,
    )

    # --- Validate quantity × unit_price ≈ total_line_value ---
    # Mismatches indicate either rounding conventions or data corruption. If we
    # trust total_line_value for revenue reporting without this check, we may
    # be aggregating inflated or deflated line values silently.
    required = {"quantity", "negotiated_unit_price", "total_line_value"}
    if required.issubset(df.columns):
        expected = df["quantity"] * df["negotiated_unit_price"]
        mismatch = (expected - df["total_line_value"]).abs() > 0.02  # tolerance: 2 cents
        mismatch = mismatch & expected.notna() & df["total_line_value"].notna()
        if mismatch.any():
            warnings.warn(
                f"[{FILE}] {mismatch.sum()} rows: quantity × negotiated_unit_price ≠ total_line_value.",
                UserWarning,
            )
            _append_issue(
                issues_log, FILE, "MISMATCH", int(mismatch.sum()), "FLAGGED",
                "quantity × negotiated_unit_price does not equal total_line_value (>$0.02 delta). "
                "App uses quantity × negotiated_unit_price as the authoritative line revenue.",
            )
            # Recompute total_line_value from components so aggregations are internally consistent
            df.loc[mismatch, "total_line_value"] = expected[mismatch]

    # --- Normalize order_status ---
    # Mixed casing ('shipped' vs 'Shipped') creates phantom status buckets in charts.
    if "order_status" in df.columns:
        df["order_status"] = df["order_status"].str.title()

    # --- Normalize region ---
    df = _normalize_region_column(df, "region", FILE, issues_log)

    # --- Derive fiscal_quarter from order_date for use in target joins ---
    # The regional_targets table keys on fiscal_quarter. Deriving it here once,
    # centrally, prevents each page from implementing its own quarter logic
    # and producing inconsistent mappings.
    if "order_date" in df.columns:
        df["fiscal_quarter"] = df["order_date"].apply(_derive_fiscal_quarter)

    return df


def _load_agreements(issues_log: list) -> pd.DataFrame | None:
    """
    Load and clean pricing_agreements.csv.

    Columns: agreement_id, customer_id, sku, tier_min_qty, tier_max_qty,
             contracted_price, effective_date, expiry_date, discount_pct,
             currency, status
    """
    FILE = "pricing_agreements.csv"
    df = _safe_read_csv(DATA_DIR / FILE, issues_log)
    if df is None:
        return None

    # --- Strip whitespace ---
    df = df.apply(lambda col: col.str.strip() if col.dtype == "object" else col)

    # --- Normalize join keys to match orders and catalog ---
    if "sku" in df.columns:
        df["sku"] = df["sku"].str.upper()
    if "customer_id" in df.columns:
        df["customer_id"] = df["customer_id"].str.upper().str.strip()

    # --- Deduplicate on agreement_id ---
    if "agreement_id" in df.columns:
        before = len(df)
        df = df.drop_duplicates(subset=["agreement_id"], keep="first")
        dupes = before - len(df)
        if dupes > 0:
            _append_issue(
                issues_log, FILE, "DUPLICATE", dupes, "DROPPED",
                "Duplicate agreement_id rows — keeping first occurrence.",
            )

    # --- Parse date columns ---
    df = _parse_dates(df, ["effective_date", "expiry_date"], FILE, issues_log)

    # --- Cast numeric columns ---
    df = _cast_numeric(
        df,
        ["tier_min_qty", "tier_max_qty", "contracted_price", "discount_pct"],
        FILE, issues_log, allow_negative=False,
    )

    # --- Normalize currency to uppercase ---
    # Currency comparisons and filtering are case-sensitive. 'usd' vs 'USD'
    # would cause the non-USD exclusion logic below to silently miss rows.
    if "currency" in df.columns:
        df["currency"] = df["currency"].str.upper()

    # --- Flag non-USD agreements ---
    # Compliance analysis compares contracted_price to negotiated_unit_price,
    # which is always in USD per the orders schema. Comparing across currencies
    # without conversion would produce nonsensical compliance flags.
    if "currency" in df.columns:
        non_usd = df["currency"].notna() & (df["currency"] != "USD")
        if non_usd.any():
            warnings.warn(
                f"[{FILE}] {non_usd.sum()} agreements are not in USD. "
                "They are retained but excluded from compliance rate calculations.",
                UserWarning,
            )
            _append_issue(
                issues_log, FILE, "ANOMALY", int(non_usd.sum()), "EXCLUDED_FROM_ANALYSIS",
                "Non-USD agreements excluded from pricing compliance calculations "
                "to avoid cross-currency comparisons without FX rates.",
            )
        df["is_usd"] = ~non_usd

    # --- Normalize status ---
    if "status" in df.columns:
        df["status"] = df["status"].str.title()

    # --- Flag expired agreements ---
    # An expired agreement is still a valid historical data point but should not
    # drive compliance flags for orders placed after expiry. Flagging here lets
    # the compliance page optionally filter to active-at-order-date agreements.
    if "expiry_date" in df.columns:
        today = pd.Timestamp.today().normalize()
        expired = df["expiry_date"].notna() & (df["expiry_date"] < today)
        if expired.any():
            _append_issue(
                issues_log, FILE, "ANOMALY", int(expired.sum()), "FLAGGED",
                f"Agreements with expiry_date in the past ({expired.sum()} rows). "
                "Retained; compliance page can filter by agreement status.",
            )

    return df


def _load_targets(issues_log: list) -> pd.DataFrame | None:
    """
    Load and clean regional_targets.csv.

    Columns: region, product_family, fiscal_quarter, revenue_target_usd,
             units_target, asp_target, growth_pct_yoy
    """
    FILE = "regional_targets.csv"
    df = _safe_read_csv(DATA_DIR / FILE, issues_log)
    if df is None:
        return None

    # --- Strip whitespace ---
    df = df.apply(lambda col: col.str.strip() if col.dtype == "object" else col)

    # --- Normalize region ---
    df = _normalize_region_column(df, "region", FILE, issues_log)

    # --- Normalize fiscal_quarter to canonical 'YYYY-QN' format ---
    # Targets join to orders on (region, product_family, fiscal_quarter).
    # If 'Q1 2024' in targets doesn't match '2024-Q1' derived from order_date,
    # every target join will fail and attainment will show as 0% everywhere.
    if "fiscal_quarter" in df.columns:
        before_nulls = df["fiscal_quarter"].isna().sum()
        df["fiscal_quarter"] = df["fiscal_quarter"].apply(_parse_fiscal_quarter_string)
        after_nulls = df["fiscal_quarter"].isna().sum()
        unparsed = after_nulls - before_nulls
        if unparsed > 0:
            warnings.warn(
                f"[{FILE}] {unparsed} fiscal_quarter values could not be parsed.",
                UserWarning,
            )
            _append_issue(
                issues_log, FILE, "FORMAT", unparsed, "COERCED_TO_NAN",
                "fiscal_quarter strings in unrecognized formats set to NaN.",
            )

    # --- Normalize product_family casing ---
    # product_family is a join key between targets and the catalog-enriched orders.
    # Inconsistent casing causes silent join misses.
    if "product_family" in df.columns:
        df["product_family"] = df["product_family"].str.title()

# Strip commas from revenue_target_usd before casting (e.g. "3,408,643" -> 3408643)
    if "revenue_target_usd" in df.columns:
        df["revenue_target_usd"] = df["revenue_target_usd"].astype(str).str.replace(",", "", regex=False)
    
# --- Cast numeric columns ---
    df = _cast_numeric(
        df,
        ["revenue_target_usd", "units_target", "asp_target", "growth_pct_yoy"],
        FILE, issues_log, allow_negative=False,
    )

    # --- Deduplicate on (region, product_family, fiscal_quarter) ---
    # Duplicate target rows would cause double-counting when comparing actuals,
    # making attainment appear ~50% of what it actually is.
    pk_cols = [c for c in ["region", "product_family", "fiscal_quarter"] if c in df.columns]
    if pk_cols:
        before = len(df)
        df = df.drop_duplicates(subset=pk_cols, keep="first")
        dupes = before - len(df)
        if dupes > 0:
            _append_issue(
                issues_log, FILE, "DUPLICATE", dupes, "DROPPED",
                f"Duplicate (region, product_family, fiscal_quarter) combinations — keeping first.",
            )

    return df


# ---------------------------------------------------------------------------
# Quarter helpers
# ---------------------------------------------------------------------------

def _derive_fiscal_quarter(dt) -> str | None:
    """
    Convert a Timestamp to canonical 'YYYY-QN' string.
    Returns None if the input is NaT or None.

    Used to tag each order row so it can join to regional_targets.
    """
    if pd.isna(dt):
        return None
    q = (dt.month - 1) // 3 + 1
    return f"{dt.year}-Q{q}"


def _parse_fiscal_quarter_string(raw: str) -> str | None:
    """
    Parse a variety of fiscal quarter string formats to canonical 'YYYY-QN'.

    Handles: 'Q1 2024', '2024-Q1', '2024Q1', 'FY24Q1', 'FY2024Q1', '1Q24'
    Returns None for anything unrecognized.
    """
    import re
    if pd.isna(raw) or not isinstance(raw, str):
        return None
    s = raw.strip().upper()

    # Pattern: Q1 2024 or Q1-2024
    m = re.match(r"Q([1-4])[\s\-]+(\d{4})", s)
    if m:
        return f"{m.group(2)}-Q{m.group(1)}"

    # Pattern: 2024-Q1 or 2024Q1
    m = re.match(r"(\d{4})[\-]?Q([1-4])", s)
    if m:
        return f"{m.group(1)}-Q{m.group(2)}"

    # Pattern: FY2024-Q1
    m = re.match(r"FY(\d{4})-Q([1-4])", s)
    if m:
        return f"{m.group(1)}-Q{m.group(2)}"

    # Pattern: Q1-FY2024
    m = re.match(r"Q([1-4])-FY(\d{4})", s)
    if m:
        return f"{m.group(2)}-Q{m.group(1)}"

	# Pattern: FY2024Q1 or FY24Q1
    m = re.match(r"FY(\d{2,4})Q([1-4])", s)
    if m:
        year = m.group(1)
        year = "20" + year if len(year) == 2 else year
        return f"{year}-Q{m.group(2)}"

    # Pattern: 1Q24 or 1Q2024
    m = re.match(r"([1-4])Q(\d{2,4})", s)
    if m:
        year = m.group(2)
        year = "20" + year if len(year) == 2 else year
        return f"{year}-Q{m.group(1)}"

    return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

@st.cache_data
def load_all_data() -> tuple[dict[str, pd.DataFrame], list[dict]]:
    """
    Load, clean, and return all four ChipMetrics datasets.

    Returns
    -------
    dataframes : dict
        Keys: 'catalog', 'orders', 'agreements', 'targets'
        Values: cleaned DataFrames (None if the source file was missing)

    issues_log : list[dict]
        One dict per data quality event with keys:
            file, issue_type, count, action_taken, description
        Pass this directly to st.dataframe() on the Data Quality page.

    Usage
    -----
        dfs, issues = load_all_data()
        orders = dfs["orders"]
        st.dataframe(pd.DataFrame(issues))
    """
    issues_log: list[dict] = []

    catalog    = _load_catalog(issues_log)
    orders     = _load_orders(issues_log)
    agreements = _load_agreements(issues_log)
    targets    = _load_targets(issues_log)

    # --- Cross-file: flag orphan SKUs in orders ---
    # Orders referencing SKUs absent from the catalog will produce NaN for
    # product_family, making those rows invisible in all family-level analyses.
    if orders is not None and catalog is not None:
        orphan_sku_mask = ~orders["sku"].isin(catalog["sku"])
        if orphan_sku_mask.any():
            warnings.warn(
                f"[customer_orders.csv] {orphan_sku_mask.sum()} order lines reference SKUs "
                "not found in product_catalog. They will have no product_family.",
                UserWarning,
            )
            _append_issue(
                issues_log,
                file="customer_orders.csv",
                issue_type="ORPHAN",
                count=int(orphan_sku_mask.sum()),
                action_taken="FLAGGED",
                description="Order lines with SKUs absent from product_catalog — "
                            "will show as NaN in product_family column after join.",
            )

    # --- Cross-file: flag orphan customer_ids in agreements ---
    # If a pricing agreement references a customer_id that never appears in
    # orders, that agreement is dormant and will never match during compliance.
    if agreements is not None and orders is not None:
        orphan_cust_mask = ~agreements["customer_id"].isin(orders["customer_id"])
        if orphan_cust_mask.any():
            _append_issue(
                issues_log,
                file="pricing_agreements.csv",
                issue_type="ORPHAN",
                count=int(orphan_cust_mask.sum()),
                action_taken="FLAGGED",
                description="Agreements for customer_ids with no matching orders — "
                            "these agreements will never match during compliance analysis.",
            )

    dataframes = {
        "catalog":    catalog,
        "orders":     orders,
        "agreements": agreements,
        "targets":    targets,
    }

    return dataframes, issues_log
