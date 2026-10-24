import sqlite3

import pandas as pd
import pytest

from app_config import config
from src import database, queries


def test_tables_and_indexes_exist(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','index')")}
    assert {"sales_lines", "dim_product", "dim_customer", "idx_sales_date", "idx_sales_region"} <= names


def test_database_reconciles_with_the_clean_table(clean_df, db_path):
    check = database.reconcile_with_clean(clean_df, db_path)
    assert check["ok"], check
    assert check["rows"][0] == len(clean_df)


def test_dimension_tables_have_one_row_per_key(conn, clean_df):
    assert conn.execute("SELECT COUNT(*) FROM dim_product").fetchone()[0] == clean_df["product_id"].nunique()
    assert conn.execute("SELECT COUNT(*) FROM dim_customer").fetchone()[0] == clean_df["customer_id"].nunique()


def test_dates_are_stored_as_iso_text(conn):
    first, last = conn.execute("SELECT MIN(order_date), MAX(order_date) FROM sales_lines").fetchone()
    assert first == config.START_DATE and last == config.END_DATE
    assert conn.execute("SELECT typeof(order_date) FROM sales_lines LIMIT 1").fetchone()[0] == "text"


def test_primary_key_blocks_a_duplicate_order_line(conn):
    row = conn.execute("SELECT * FROM sales_lines LIMIT 1").fetchone()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO sales_lines VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)


def test_foreign_key_blocks_an_unknown_customer(conn):
    row = list(conn.execute("SELECT * FROM sales_lines LIMIT 1").fetchone())
    row[0], row[3] = "ORD-NEW", "C-DOES-NOT-EXIST"
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO sales_lines VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)


def test_check_constraints_block_bad_values(conn):
    base = list(conn.execute("SELECT * FROM sales_lines LIMIT 1").fetchone())
    for index, bad in ((9, 0), (10, -1.0), (12, -5.0), (2, "05/01/2025")):   # quantity, price, cost, date
        row = list(base)
        row[0] = f"ORD-BAD-{index}"
        row[index] = bad
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO sales_lines VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)


def test_rebuilding_replaces_the_database_instead_of_appending(clean_df, tmp_path):
    path = tmp_path / "twice.db"
    database.build_database(clean_df, path)
    database.build_database(clean_df, path)
    conn = database.connect(path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM sales_lines").fetchone()[0] == len(clean_df)
    finally:
        conn.close()


def test_build_fails_when_a_product_has_two_categories(tiny_df, tmp_path):
    broken = tiny_df.copy()
    broken.loc[0, "category"] = "Clothing"          # P1 is now both Electronics and Clothing
    with pytest.raises(ValueError, match="product_id has more than one value"):
        database.build_database(broken, tmp_path / "x.db")


def test_build_fails_when_a_customer_has_two_regions(tiny_df, tmp_path):
    broken = tiny_df.copy()
    broken.loc[0, "region"] = "East"
    with pytest.raises(ValueError, match="customer_id has more than one value"):
        database.build_database(broken, tmp_path / "x.db")


def test_build_fails_when_columns_are_missing(tiny_df, tmp_path):
    with pytest.raises(ValueError, match="missing columns"):
        database.build_database(tiny_df.drop(columns=["cost"]), tmp_path / "x.db")


def test_connect_reports_a_missing_database(tmp_path):
    with pytest.raises(FileNotFoundError, match="run_pipeline"):
        database.connect(tmp_path / "nope.db")


def test_load_clean_sales_reports_a_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="run_pipeline"):
        database.load_clean_sales(tmp_path / "nope.csv")


def test_missing_sql_file_is_reported(conn):
    with pytest.raises(FileNotFoundError):
        queries.load_sql("does_not_exist")


def test_every_sql_file_can_be_loaded_and_documents_its_parameters():
    for name in ["kpi_summary", "monthly_trend", "by_region", "by_category", "by_segment",
                 "by_product", "region_monthly", "customer_metrics", "period_comparison"]:
        text = queries.load_sql(name)
        assert text.lstrip().startswith("--"), f"{name}.sql should start with a comment"
        assert "Parameters" in text
        assert "SELECT" in text.upper()
