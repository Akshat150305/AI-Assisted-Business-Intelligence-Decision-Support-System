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

SQL_DIR = PROJECT_ROOT / "sql"
DB_FILE = DATA_PROCESSED / "retail_bi.db"       # SQLite database (git-ignored, rebuilt by the pipeline)

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

# ---------------------------------------------------------------- anomaly detection (Phase 4)
# Parameter history (documented honestly, see reports/anomaly_evaluation.md):
#   1. First choice, fixed before the detector was ever run: window 8 weeks, threshold 3.5.
#   2. On the real data that flagged 6.1% of weeks. A simulation on PURE NOISE (no project data and
#      no planted labels) showed the same problem: with only 8 weeks of history the robust spread
#      is itself very noisy, so |z| >= 3.5 occurs in ~4.8% of weeks even when nothing is wrong.
#   3. Revised using that noise simulation only: a 13-week (one quarter) window and a threshold of
#      4.5 give about a 1% false-alarm rate on noise. The planted labels played no part in this choice.
ANOMALY_WINDOW_WEEKS = 13       # baseline = the previous 13 complete weeks (one quarter)
ANOMALY_MIN_HISTORY_WEEKS = 8   # fewer than 8 valid previous weeks -> "insufficient history", never flagged
ANOMALY_THRESHOLD = 4.5         # |robust z| >= 4.5 is flagged
ANOMALY_SEVERITY_BANDS = (8.0, 15.0)  # |z| below 8 = moderate, 8 to 15 = high, 15 or more = extreme
ANOMALY_METRICS = ["revenue", "profit", "profit_margin_pct"]

ANOMALY_WEEKLY_EXPORT = DATA_EXPORTS / "anomaly_weekly.csv"
ANOMALY_EVALUATION_REPORT = REPORTS_DIR / "anomaly_evaluation.md"

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
