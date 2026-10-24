"""Power BI-ready CSV exports (star schema) written to data/exports/.

Tables
------
fact_sales.csv         one row per order line: keys, date and the numeric measures only
dim_date.csv           one row per calendar day with year, quarter, month, week, weekday ...
dim_product.csv        product_id, product_name, category
dim_customer.csv       customer_id, customer_segment, region
kpi_summary.csv        overall KPIs from SQL   -> used to CHECK the DAX measures in Power BI
kpi_monthly.csv        monthly KPIs from SQL   -> used to CHECK the DAX measures by month
customer_metrics.csv   per-customer metrics from SQL (customer analysis page)

Data ownership (docs/requirements.md, 7.3): Power BI recalculates the standard KPIs with DAX from
fact_sales. The kpi_* tables are the answer key those DAX results must match. Anomalies and findings
are added in later phases as separate pre-computed tables.

Run from the project root:   python -m src.powerbi_export
ALL DATA IS SYNTHETIC.
"""
import pandas as pd

from app_config import config
from src import database, queries

FACT_COLUMNS = ["order_id", "line_number", "order_date", "customer_id", "product_id",
                "quantity", "unit_price", "revenue", "cost", "profit"]
EXPORT_FILES = ["fact_sales.csv", "dim_date.csv", "dim_product.csv", "dim_customer.csv",
                "kpi_summary.csv", "kpi_monthly.csv", "customer_metrics.csv"]


def build_dim_date(start, end):
    """Complete calendar (no gaps) from start to end, which Power BI time intelligence requires."""
    days = pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="D")
    dim = pd.DataFrame({"date": days})
    dim["year"] = days.year
    dim["quarter"] = "Q" + days.quarter.astype(str)
    dim["month_number"] = days.month
    dim["month_name"] = days.strftime("%B")
    dim["year_month"] = days.strftime("%Y-%m")
    dim["month_start"] = (days - pd.to_timedelta(days.day - 1, unit="D")).strftime("%Y-%m-%d")
    dim["week_start"] = (days - pd.to_timedelta(days.day_of_week, unit="D")).strftime("%Y-%m-%d")  # Monday
    dim["day_of_week_number"] = days.day_of_week + 1          # Monday = 1
    dim["day_name"] = days.strftime("%A")
    dim["is_weekend"] = (days.day_of_week >= 5).astype(int)
    dim["date"] = days.strftime("%Y-%m-%d")
    return dim


def export_all(conn, out_dir=config.DATA_EXPORTS, start=config.START_DATE, end=config.END_DATE):
    """Write every export table from the database. Returns {filename: row count}."""
    out_dir.mkdir(parents=True, exist_ok=True)

    sales = pd.read_sql_query(
        "SELECT order_id, line_number, order_date, customer_id, product_id, quantity, "
        "unit_price, revenue, cost, profit FROM sales_lines ORDER BY order_date, order_id, line_number",
        conn)[FACT_COLUMNS]
    dim_product = pd.read_sql_query("SELECT * FROM dim_product ORDER BY product_id", conn)
    dim_customer = pd.read_sql_query("SELECT * FROM dim_customer ORDER BY customer_id", conn)
    dim_date = build_dim_date(start, end)

    summary = pd.DataFrame([queries.kpi_summary(conn)])
    monthly = queries.monthly_trend(conn)
    customers = queries.customer_metrics(conn)

    tables = {
        "fact_sales.csv": sales,
        "dim_date.csv": dim_date,
        "dim_product.csv": dim_product,
        "dim_customer.csv": dim_customer,
        # ratios keep 4 decimals; money columns are already 2 decimals
        "kpi_summary.csv": summary.round(4),
        "kpi_monthly.csv": monthly.round(4),
        "customer_metrics.csv": customers.round(4),
    }
    for name, table in tables.items():
        table.to_csv(out_dir / name, index=False)
    return {name: len(table) for name, table in tables.items()}


def main():
    conn = database.connect()
    try:
        counts = export_all(conn)
    finally:
        conn.close()
    print("SYNTHETIC DATA - not real company data")
    print(f"Power BI exports written to {config.DATA_EXPORTS}")
    for name, n in counts.items():
        print(f"  {name:<22} {n:>8,} rows")


if __name__ == "__main__":
    main()
