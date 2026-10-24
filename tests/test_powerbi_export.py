import math

import pandas as pd
import pytest

from src import metrics, powerbi_export, queries
from src.filters import SalesFilter


@pytest.fixture(scope="module")
def exported(db_path, tmp_path_factory):
    """Run the export once into a temp folder and load every table back from the CSV files."""
    from src import database
    out = tmp_path_factory.mktemp("exports")
    connection = database.connect(db_path)
    try:
        counts = powerbi_export.export_all(connection, out_dir=out)
        sql_totals = queries.kpi_summary(connection)
        sql_by_region = queries.by_region(connection)
    finally:
        connection.close()
    tables = {name.replace(".csv", ""): pd.read_csv(out / name) for name in powerbi_export.EXPORT_FILES}
    return {"dir": out, "counts": counts, "tables": tables, "totals": sql_totals, "by_region": sql_by_region}


def close(a, b, tol=1e-6):
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=tol)


def test_every_export_file_is_written_with_rows(exported):
    assert sorted(exported["counts"]) == sorted(powerbi_export.EXPORT_FILES)
    assert all(n > 0 for n in exported["counts"].values())
    assert all((exported["dir"] / name).exists() for name in powerbi_export.EXPORT_FILES)


def test_fact_table_matches_the_database_totals(exported, clean_df):
    fact = exported["tables"]["fact_sales"]
    totals = exported["totals"]
    assert len(fact) == len(clean_df) == totals["order_lines"]
    assert close(fact["revenue"].sum(), totals["revenue"], tol=0.01)
    assert close(fact["profit"].sum(), totals["profit"], tol=0.01)
    assert fact["order_id"].nunique() == totals["distinct_orders"]


def test_fact_table_is_a_star_schema_with_no_descriptive_columns(exported):
    fact = exported["tables"]["fact_sales"]
    assert list(fact.columns) == powerbi_export.FACT_COLUMNS
    for descriptive in ("region", "category", "customer_segment", "product_name"):
        assert descriptive not in fact.columns


def test_fact_keys_are_unique_and_all_relationships_resolve(exported):
    t = exported["tables"]
    fact = t["fact_sales"]
    assert not fact.duplicated(["order_id", "line_number"]).any()
    assert set(fact["customer_id"]) <= set(t["dim_customer"]["customer_id"])
    assert set(fact["product_id"]) <= set(t["dim_product"]["product_id"])
    assert set(fact["order_date"]) <= set(t["dim_date"]["date"])
    assert t["dim_customer"]["customer_id"].is_unique and t["dim_product"]["product_id"].is_unique


def test_dim_date_is_a_complete_calendar():
    dim = powerbi_export.build_dim_date("2024-01-01", "2025-12-31")
    dates = pd.to_datetime(dim["date"])
    assert len(dim) == 731 and dates.is_unique and (dates.diff().dropna() == pd.Timedelta(days=1)).all()
    assert (pd.to_datetime(dim["week_start"]).dt.day_of_week == 0).all()          # weeks start on Monday
    assert (pd.to_datetime(dim["month_start"]).dt.day == 1).all()
    row = dim[dim["date"] == "2024-02-29"].iloc[0]                              # leap day
    assert row["month_name"] == "February" and row["quarter"] == "Q1" and row["year_month"] == "2024-02"
    assert row["day_name"] == "Thursday" and row["is_weekend"] == 0 and row["day_of_week_number"] == 4
    assert dim.loc[dim["date"] == "2024-03-02", "is_weekend"].iloc[0] == 1


def test_kpi_summary_export_matches_python_kpis(exported, clean_df):
    exported_kpis = exported["tables"]["kpi_summary"].iloc[0]
    python_kpis = metrics.compute_kpis(clean_df)
    for name in metrics.KPI_NAMES:
        assert close(exported_kpis[name], python_kpis[name], tol=1e-3), name


def test_kpi_monthly_export_adds_up_to_the_fact_table(exported):
    monthly, fact = exported["tables"]["kpi_monthly"], exported["tables"]["fact_sales"]
    assert close(monthly["revenue"].sum(), fact["revenue"].sum(), tol=0.05)
    assert monthly["order_lines"].sum() == len(fact)
    assert len(monthly) == 24


def test_customer_metrics_export_covers_every_customer_once(exported):
    customers, fact = exported["tables"]["customer_metrics"], exported["tables"]["fact_sales"]
    assert customers["customer_id"].is_unique
    assert set(customers["customer_id"]) == set(fact["customer_id"])
    assert close(customers["revenue"].sum(), fact["revenue"].sum(), tol=0.5)


def test_power_bi_style_join_gives_the_same_figures_as_sql(exported, conn):
    """Rebuild a filtered KPI the way Power BI will (fact joined to the dimensions) and compare with SQL."""
    t = exported["tables"]
    joined = (t["fact_sales"].merge(t["dim_customer"], on="customer_id")
              .merge(t["dim_product"], on="product_id"))
    for region, category in (("West", None), ("East", "Electronics")):
        sub = joined[joined["region"] == region]
        if category:
            sub = sub[sub["category"] == category]
        sql = queries.kpi_summary(conn, SalesFilter(region=region, category=category))
        assert close(sub["revenue"].sum(), sql["revenue"], tol=0.01)
        assert sub["order_id"].nunique() == sql["distinct_orders"]
        assert close(sub["revenue"].sum() / sub["order_id"].nunique(), sql["avg_order_value"], tol=1e-3)


def test_exports_contain_no_test_labels(exported):
    for name, table in exported["tables"].items():
        for column in table.columns:
            assert "anomaly" not in column.lower() and "defect" not in column.lower(), (name, column)
