"""
app.py
------
ChipMetrics Pricing Intelligence Platform — Entry Point.

Loads all data once (cached), renders the sidebar navigation, and routes to
the selected page module. Page modules import load_all_data() independently
but benefit from Streamlit's @st.cache_data — the pipeline only runs once
per session regardless of how many pages call it.

Run with:
    streamlit run app.py
"""

import streamlit as st
import sys
from pathlib import Path

# Ensure src/ is on the path for all imports
sys.path.append(str(Path(__file__).parent))

from src.data_loader import load_all_data

# ---------------------------------------------------------------------------
# Global page config — must be the first Streamlit call in the entry point
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="ChipMetrics",
    page_icon="",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Load data once for the session
# @st.cache_data lives inside load_all_data(), so this call is a cache hit
# for every subsequent page render within the same session.
# ---------------------------------------------------------------------------
dfs, issues_log = load_all_data()

# ---------------------------------------------------------------------------
# Sidebar — branding + navigation
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown(
        """
        <div style="padding: 8px 0 20px 0;">
            <span style="font-size: 1.6rem; font-weight: 800; letter-spacing: -0.5px;">
                 ChipMetrics
            </span><br>
            <span style="font-size: 0.8rem; color: #6F6F6F; line-height: 1.4;">
                Pricing Intelligence Platform
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("---")
    st.markdown("**Navigation**")

    PAGE_ORDER_ANALYTICS    = " Order Analytics"
    PAGE_PRICING_COMPLIANCE = " Pricing Compliance"
    PAGE_DATA_QUALITY       = " Data Quality Report"
    PAGE_TARGET_VS_ACTUAL   = " Target vs. Actual"

    page = st.radio(
        label="Go to",
        options=[
            PAGE_ORDER_ANALYTICS,
            PAGE_PRICING_COMPLIANCE,
            PAGE_DATA_QUALITY,
            PAGE_TARGET_VS_ACTUAL,
        ],
        label_visibility="collapsed",
    )

    st.markdown("---")

    # Data health summary in the sidebar — visible from any page
    n_issues = len(issues_log)
    n_files_missing = sum(
        1 for i in issues_log if i.get("issue_type") == "MISSING_FILE"
    )

    if n_files_missing > 0:
        st.error(f" {n_files_missing} source file(s) missing")
    elif n_issues == 0:
        st.success(" Data loaded cleanly")
    else:
        st.info(f"ℹ {n_issues} cleaning events logged")

    st.caption(
        f"{sum(1 for d in dfs.values() if d is not None)}/4 source files loaded"
    )

# ---------------------------------------------------------------------------
# Platform header — shown on every page above the selected content
# ---------------------------------------------------------------------------
st.markdown(
    """
    <div style="
        background: linear-gradient(90deg, #0F62FE 0%, #0043CE 100%);
        padding: 18px 28px;
        border-radius: 6px;
        margin-bottom: 24px;
        display: flex;
        align-items: center;
        gap: 16px;
    ">
        <div>
            <div style="font-size:1.35rem; font-weight:800; color:#ffffff; letter-spacing:-0.3px;">
                 ChipMetrics Pricing Intelligence Platform
            </div>
            <div style="font-size:0.88rem; color:#a6c8ff; margin-top:3px;">
                Analyze order trends, enforce pricing agreements, and track regional
                performance — built for semiconductor sales and operations teams.
            </div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Page routing — import and run the selected page module
# ---------------------------------------------------------------------------
import importlib.util

def _run_page(module_path: str) -> None:
    """
    Execute a page module in the current interpreter context.

    Each page module calls st.set_page_config() so it can also be run
    standalone. When loaded via app.py, that call would raise an error
    because app.py already owns the single allowed set_page_config() call.
    We patch it out temporarily before exec and restore it after.
    """
    spec   = importlib.util.spec_from_file_location("_page", module_path)
    module = importlib.util.module_from_spec(spec)
    original = st.set_page_config
    st.set_page_config = lambda **kwargs: None
    try:
        spec.loader.exec_module(module)
    finally:
        st.set_page_config = original


PAGES_DIR = Path(__file__).parent / "pages"

PAGE_MODULES = {
    PAGE_ORDER_ANALYTICS:    PAGES_DIR / "01_order_analytics.py",
    PAGE_PRICING_COMPLIANCE: PAGES_DIR / "02_pricing_compliance.py",
    PAGE_DATA_QUALITY:       PAGES_DIR / "03_data_quality.py",
    PAGE_TARGET_VS_ACTUAL:   PAGES_DIR / "04_target_vs_actual.py",
}

if page in PAGE_MODULES:
    _run_page(str(PAGE_MODULES[page]))
else:
    st.error(f"Unknown page: {page}")
