"""SQLite database creation.

Reads the CLEAN sales table, builds the schema from sql/01_schema.sql, and loads the dimension
tables and the fact table. The database file is rebuilt from scratch on every run, so it always
matches the clean data exactly.

Run from the project root:   python -m src.database
ALL DATA IS SYNTHETIC.
"""
import sqlite3

import pandas as pd

from app_config import config

SCHEMA_FILE = config.SQL_DIR / "01_schema.sql"
FACT_COLUMNS = config.SALES_COLUMNS      # same 14 columns, same order, as the clean CSV


def load_clean_sales(path=config.CLEAN_SALES_FILE):
    """Read the cleaned CSV with proper types."""
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run 'python run_pipeline.py' first.")
    return pd.read_csv(path, parse_dates=["order_date"])


def connect(db_path=config.DB_FILE):
    """Open the database with foreign keys switched on (SQLite has them off by default)."""
    if not db_path.exists():
        raise FileNotFoundError(f"{db_path} not found. Run 'python run_pipeline.py' first.")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _build_dimension(df, key, attributes):
    """One row per key. Raises if an attribute is not unique per key (that would be a data error)."""
    dim = df[[key] + attributes].drop_duplicates()
    if dim[key].duplicated().any():
        bad = dim[dim[key].duplicated(keep=False)][key].unique()[:5].tolist()
        raise ValueError(f"{key} has more than one value of {attributes} (examples: {bad})")
    return dim.sort_values(key).reset_index(drop=True)


def build_database(clean=None, db_path=config.DB_FILE):
    """Create (or recreate) the SQLite database from the clean sales table.

    Returns the path of the database file.
    """
    if clean is None:
        clean = load_clean_sales()
    missing = [c for c in FACT_COLUMNS if c not in clean.columns]
    if missing:
        raise ValueError(f"Clean sales table is missing columns: {missing}")

    clean = clean[FACT_COLUMNS].copy()
    clean["order_date"] = pd.to_datetime(clean["order_date"]).dt.strftime("%Y-%m-%d")
    dim_product = _build_dimension(clean, "product_id", ["product_name", "category"])
    dim_customer = _build_dimension(clean, "customer_id", ["customer_segment", "region"])

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        # Dimensions first, because the fact table has foreign keys to them.
        dim_product.to_sql("dim_product", conn, if_exists="append", index=False)
        dim_customer.to_sql("dim_customer", conn, if_exists="append", index=False)
        clean.to_sql("sales_lines", conn, if_exists="append", index=False)
        conn.commit()
    finally:
        conn.close()
    return db_path


def reconcile_with_clean(clean, db_path=config.DB_FILE):
    """Compare row count and money totals between the clean table and the database.

    Returns a dict. ``ok`` is True when they agree.
    """
    conn = sqlite3.connect(db_path)
    try:
        rows, revenue, profit, orders = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(revenue),0), COALESCE(SUM(profit),0), "
            "COUNT(DISTINCT order_id) FROM sales_lines").fetchone()
    finally:
        conn.close()
    result = {
        "rows": (len(clean), rows),
        "revenue": (round(float(clean["revenue"].sum()), 2), round(revenue, 2)),
        "profit": (round(float(clean["profit"].sum()), 2), round(profit, 2)),
        "distinct_orders": (int(clean["order_id"].nunique()), orders),
    }
    result["ok"] = all(a == b for a, b in result.values())
    return result


def main():
    clean = load_clean_sales()
    path = build_database(clean)
    check = reconcile_with_clean(clean, path)
    print("SYNTHETIC DATA - not real company data")
    print(f"database written to : {path}")
    for name in ("rows", "revenue", "profit", "distinct_orders"):
        csv_value, db_value = check[name]
        print(f"{name:<16}: clean csv = {csv_value:,}   database = {db_value:,}")
    print(f"database matches clean data: {check['ok']}")
    if not check["ok"]:
        raise SystemExit("Database does not match the clean data")


if __name__ == "__main__":
    main()
