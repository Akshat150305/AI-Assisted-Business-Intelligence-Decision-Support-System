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
