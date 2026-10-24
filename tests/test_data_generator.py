import numpy as np
import pandas as pd
import pytest

from app_config import config
from src import data_generator


# ----------------------------------------------------------------------------- reproducibility
def test_same_seed_gives_identical_data(datasets):
    again = data_generator.build_datasets()
    pd.testing.assert_frame_equal(datasets["truth"], again["truth"])
    pd.testing.assert_frame_equal(datasets["raw"], again["raw"])
    pd.testing.assert_frame_equal(datasets["defect_labels"], again["defect_labels"])
    pd.testing.assert_frame_equal(datasets["anomaly_labels"], again["anomaly_labels"])


def test_different_seed_gives_different_data(datasets):
    other = data_generator.build_datasets(seed=7)
    assert not other["truth"].equals(datasets["truth"])


# ----------------------------------------------------------------------------- truth data is internally valid
def test_truth_columns_and_no_missing_values(datasets):
    truth = datasets["truth"]
    assert list(truth.columns) == config.SALES_COLUMNS
    assert not truth.isna().any().any()


def test_truth_money_columns_are_consistent(datasets):
    t = datasets["truth"]
    assert (t["revenue"] - (t["quantity"] * t["unit_price"]).round(2)).abs().max() < 0.005
    assert (t["profit"] - (t["revenue"] - t["cost"]).round(2)).abs().max() < 0.005
    assert (t["quantity"] > 0).all() and (t["unit_price"] > 0).all() and (t["cost"] > 0).all()


def test_truth_business_keys_and_order_structure(datasets):
    t = datasets["truth"]
    assert not t.duplicated(["order_id", "line_number"]).any()
    # An order has exactly one customer, one date and one region (requirements assumption A2).
    per_order = t.groupby("order_id")[["customer_id", "order_date", "region"]].nunique()
    assert (per_order == 1).all().all()
    # A customer has one segment and one region (assumption A3).
    per_customer = t.groupby("customer_id")[["customer_segment", "region"]].nunique()
    assert (per_customer == 1).all().all()
    # Orders and order lines must differ, otherwise "distinct orders" would be untested.
    assert t["order_id"].nunique() < len(t)


def test_truth_values_are_allowed_and_dates_in_range(datasets):
    t = datasets["truth"]
    assert set(t["category"]) <= set(config.CATEGORIES)
    assert set(t["region"]) <= set(config.REGIONS)
    assert set(t["customer_segment"]) <= set(config.SEGMENTS)
    assert t["order_date"].min() >= pd.Timestamp(config.START_DATE)
    assert t["order_date"].max() <= pd.Timestamp(config.END_DATE)


def test_each_product_has_one_category_and_name(datasets):
    t = datasets["truth"]
    assert (t.groupby("product_id")[["category", "product_name"]].nunique() == 1).all().all()


# ----------------------------------------------------------------------------- planted anomalies
def test_anomaly_labels_are_separate_from_the_raw_data(datasets):
    raw, labels = datasets["raw"], datasets["anomaly_labels"]
    assert list(raw.columns) == config.SALES_COLUMNS            # no label/flag column in the raw data
    assert len(labels) == 4
    assert set(labels["anomaly_type"]) == {"revenue_spike", "revenue_drop", "margin_squeeze", "single_large_order"}


def test_planted_revenue_spike_and_drop_are_visible_in_weekly_revenue(datasets):
    t = datasets["truth"].set_index("order_date")
    weekly = t["revenue"].resample("W-SUN").sum()

    def vs_prior(week_end):
        i = weekly.index.get_loc(pd.Timestamp(week_end))
        return weekly.iloc[i] / weekly.iloc[i - 4:i].mean()

    assert vs_prior("2025-03-23") > 1.3      # A1 spike
    assert vs_prior("2025-06-15") < 0.9      # A2 drop


def test_planted_margin_squeeze_is_visible_in_electronics_margin(datasets):
    e = datasets["truth"].query("category == 'Electronics'")
    in_window = e[(e["order_date"] >= "2025-09-01") & (e["order_date"] <= "2025-09-28")]
    outside = e[(e["order_date"] >= "2025-07-01") & (e["order_date"] <= "2025-08-31")]
    margin = lambda d: d["profit"].sum() / d["revenue"].sum()
    assert margin(outside) > 0.18
    assert margin(in_window) < 0.10


def test_planted_large_order_is_much_bigger_than_a_typical_order(datasets):
    labels = datasets["anomaly_labels"].set_index("anomaly_id")
    order_id = labels.loc["A4", "affected_order_id"]
    order_revenue = datasets["truth"].groupby("order_id")["revenue"].sum()
    assert order_revenue[order_id] > 10 * order_revenue.median()


# ----------------------------------------------------------------------------- planted defects
def test_defect_label_counts_match_the_plan(datasets):
    labels = datasets["defect_labels"]
    planned = {name: count for name, count, _, _ in data_generator.DEFECT_PLAN}
    assert labels["defect_type"].value_counts().to_dict() == planned
    assert labels["defect_id"].is_unique


def test_raw_has_extra_rows_only_for_planted_duplicates(datasets):
    duplicates = sum(c for name, c, _, _ in data_generator.DEFECT_PLAN
                     if name in ("exact_duplicate_row", "conflicting_duplicate_key"))
    assert len(datasets["raw"]) == len(datasets["truth"]) + duplicates


def test_defect_labels_point_at_real_raw_rows(datasets):
    labels, raw = datasets["defect_labels"], datasets["raw"]
    assert labels["source_row"].between(1, len(raw)).all()
    assert labels["source_row"].is_unique


# ----------------------------------------------------------------------------- writing and small runs
def test_write_datasets_writes_raw_and_labels_but_not_the_truth(datasets, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_RAW", tmp_path)
    monkeypatch.setattr(config, "RAW_SALES_FILE", tmp_path / "sales_raw.csv")
    monkeypatch.setattr(config, "ANOMALY_LABELS_FILE", tmp_path / "injected_anomalies.csv")
    monkeypatch.setattr(config, "DEFECT_LABELS_FILE", tmp_path / "injected_defects.csv")
    data_generator.write_datasets(datasets)
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "injected_anomalies.csv", "injected_defects.csv", "sales_raw.csv"]
    assert len(pd.read_csv(tmp_path / "sales_raw.csv")) == len(datasets["raw"])


def test_small_run_outside_anomaly_windows_has_no_anomaly_labels():
    small = data_generator.build_datasets(start="2024-01-01", end="2024-01-31",
                                          n_customers=60, base_orders_per_day=10)
    assert small["anomaly_labels"].empty
    assert len(small["truth"]) > 0
    assert list(small["raw"].columns) == config.SALES_COLUMNS
