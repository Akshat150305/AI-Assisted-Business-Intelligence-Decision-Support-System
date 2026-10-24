"""Central configuration for the BI project.

Everything that is a "setting" (seed, dates, allowed values, file paths) lives here
so that no other module contains hidden magic numbers.

ALL DATA IN THIS PROJECT IS SYNTHETIC. It is generated from a fixed random seed and
does not represent any real company.
"""
from pathlib import Path

# ---------------------------------------------------------------- paths
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_RAW = PROJECT_ROOT / "data" / "raw"
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"
DATA_EXPORTS = PROJECT_ROOT / "data" / "exports"
REPORTS_DIR = PROJECT_ROOT / "reports"

RAW_SALES_FILE = DATA_RAW / "sales_raw.csv"
ANOMALY_LABELS_FILE = DATA_RAW / "injected_anomalies.csv"   # test labels, never used by detection
DEFECT_LABELS_FILE = DATA_RAW / "injected_defects.csv"      # test labels for validation

CLEAN_SALES_FILE = DATA_PROCESSED / "sales_clean.csv"
REJECTED_ROWS_FILE = DATA_PROCESSED / "rejected_rows.csv"
AUDIT_LOG_FILE = DATA_PROCESSED / "cleaning_audit_log.csv"
QUALITY_SUMMARY_FILE = REPORTS_DIR / "data_quality_summary.md"

# ---------------------------------------------------------------- generation
SEED = 42
START_DATE = "2024-01-01"      # first order date (inclusive)
END_DATE = "2025-12-31"        # last order date (inclusive)
N_CUSTOMERS = 1200
BASE_ORDERS_PER_DAY = 26       # average across all regions at the start of the period

# ---------------------------------------------------------------- allowed values
CATEGORIES = [
    "Electronics",
    "Home & Kitchen",
    "Clothing",
    "Sports & Outdoors",
    "Beauty & Personal Care",
]
REGIONS = ["North", "South", "East", "West"]
SEGMENTS = ["Consumer", "Small Business", "Corporate"]

# ---------------------------------------------------------------- columns
# Column order of the raw / clean sales file (one row = one order line).
SALES_COLUMNS = [
    "order_id",
    "line_number",
    "order_date",
    "customer_id",
    "customer_segment",
    "product_id",
    "product_name",
    "category",
    "region",
    "quantity",
    "unit_price",
    "revenue",
    "cost",
    "profit",
]
TEXT_COLUMNS = [
    "order_id", "customer_id", "customer_segment", "product_id",
    "product_name", "category", "region",
]
NUMERIC_COLUMNS = ["line_number", "quantity", "unit_price", "revenue", "cost", "profit"]

# ---------------------------------------------------------------- validation tolerances
MONEY_TOLERANCE = 0.01          # revenue/profit may differ from the formula by at most 1 cent
PRICE_BAND = (0.5, 2.0)         # unit price must be within 0.5x to 2x of the product's median price
