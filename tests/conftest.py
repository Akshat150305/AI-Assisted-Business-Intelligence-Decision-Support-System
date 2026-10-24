"""Shared fixtures.

Generating the full synthetic dataset takes about a second, so it is built once per test session.
"""
import pytest

from src import data_generator, data_pipeline


@pytest.fixture(scope="session")
def datasets():
    """Truth, raw data and label tables for the default seed (kept in memory, nothing written)."""
    return data_generator.build_datasets()


@pytest.fixture(scope="session")
def raw_csv_path(datasets, tmp_path_factory):
    """The raw data written exactly like data/raw/sales_raw.csv, in a temporary folder."""
    path = tmp_path_factory.mktemp("raw") / "sales_raw.csv"
    datasets["raw"].to_csv(path, index=False, float_format="%.2f")
    return path


@pytest.fixture(scope="session")
def cleaning_result(raw_csv_path):
    return data_pipeline.clean_sales(data_pipeline.load_raw_sales(raw_csv_path))


# ----------------------------------------------------------------------------- Phase 3 fixtures
import pandas as pd  # noqa: E402

from app_config import config  # noqa: E402
from src import database  # noqa: E402


@pytest.fixture(scope="session")
def clean_df(cleaning_result):
    """The cleaned sales table (datetime order_date), built from the generated raw data."""
    return cleaning_result.clean


@pytest.fixture(scope="session")
def db_path(clean_df, tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "retail_bi.db"
    database.build_database(clean_df, path)
    return path


@pytest.fixture()
def conn(db_path):
    connection = database.connect(db_path)
    yield connection
    connection.close()


# A tiny dataset small enough to calculate by hand (see tests/test_metrics.py for the arithmetic).
TINY_ROWS = [
    # order_id, line, date,        cust, segment,    product, name,   category,    region,  qty, price, revenue, cost, profit
    ("A", 1, "2025-01-10", "C1", "Consumer",  "P1", "Widget", "Electronics", "North", 2, 10.0, 20.0, 12.0, 8.0),
    ("A", 2, "2025-01-10", "C1", "Consumer",  "P2", "Shirt",  "Clothing",    "North", 1, 5.0,  5.0,  2.0,  3.0),
    ("B", 1, "2025-01-20", "C1", "Consumer",  "P1", "Widget", "Electronics", "North", 1, 10.0, 10.0, 6.0,  4.0),
    ("C", 1, "2025-02-05", "C2", "Corporate", "P2", "Shirt",  "Clothing",    "South", 4, 5.0,  20.0, 8.0,  12.0),
]


@pytest.fixture()
def tiny_df():
    df = pd.DataFrame(TINY_ROWS, columns=config.SALES_COLUMNS)
    df["order_date"] = pd.to_datetime(df["order_date"])
    return df


@pytest.fixture()
def tiny_conn(tiny_df, tmp_path):
    path = tmp_path / "tiny.db"
    database.build_database(tiny_df, path)
    connection = database.connect(path)
    yield connection
    connection.close()
