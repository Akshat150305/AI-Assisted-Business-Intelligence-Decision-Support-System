"""Unit tests for the individual validation rules, using tiny hand-made tables."""
import numpy as np
import pandas as pd

from app_config import config
from src import validation as v


def _row(**overrides):
    base = {
        "order_id": "ORD-1", "line_number": 1, "order_date": pd.Timestamp("2025-01-05"),
        "customer_id": "C1", "customer_segment": "Consumer", "product_id": "P001",
        "product_name": "Wireless Earbuds", "category": "Electronics", "region": "North",
        "quantity": 2, "unit_price": 10.0, "revenue": 20.0, "cost": 12.0, "profit": 8.0,
    }
    base.update(overrides)
    return base


def _df(*rows):
    return pd.DataFrame(list(rows), columns=config.SALES_COLUMNS)


# ------------------------------------------------------------------------- field checks
def test_invalid_quantity_flags_zero_negative_and_fractions_but_not_blank():
    q = pd.Series([1, 0, -3, 2.5, np.nan, 7])
    assert v.invalid_quantity(q).tolist() == [False, True, True, True, False, False]


def test_invalid_price_and_cost():
    assert v.invalid_price(pd.Series([5.0, 0.0, -1.0, np.nan])).tolist() == [False, True, True, False]
    assert v.invalid_cost(pd.Series([0.0, 3.0, -0.01, np.nan])).tolist() == [False, False, True, False]


def test_parse_dates_is_strict():
    parsed = v.parse_dates(pd.Series(["2025-01-31", "2025-02-30", "31/01/2025", "N/A", np.nan]))
    assert parsed.notna().tolist() == [True, False, False, False, False]


def test_date_out_of_range():
    dates = pd.Series(pd.to_datetime(["2023-12-31", "2024-01-01", "2025-12-31", "2026-01-01"]))
    assert v.date_out_of_range(dates).tolist() == [True, False, False, True]


def test_not_allowed_values_are_case_sensitive_and_ignore_blanks():
    s = pd.Series(["North", "north", " North", "Nrth", np.nan])
    assert v.not_allowed(s, config.REGIONS).tolist() == [False, True, True, True, False]


def test_revenue_and_profit_mismatch_use_a_one_cent_tolerance():
    df = _df(_row(),                                   # correct
             _row(order_id="O2", revenue=20.005),      # within tolerance
             _row(order_id="O3", revenue=25.0, profit=13.0),   # wrong revenue (profit consistent with it)
             _row(order_id="O4", profit=9.0))          # wrong profit
    assert v.revenue_mismatch(df).tolist() == [False, False, True, False]
    assert v.profit_mismatch(df).tolist() == [False, False, False, True]


def test_money_mismatch_is_not_checked_when_inputs_are_blank():
    df = _df(_row(unit_price=np.nan), _row(order_id="O2", cost=np.nan))
    assert not v.revenue_mismatch(df).any()
    assert not v.profit_mismatch(df).any()


def test_price_outlier_uses_the_products_median_price():
    rows = [_row(order_id=f"O{i}", unit_price=10.0) for i in range(5)]
    rows.append(_row(order_id="O9", unit_price=1000.0))
    df = _df(*rows)
    assert v.price_outlier(df).tolist() == [False] * 5 + [True]


# ------------------------------------------------------------------------- duplicates and order structure
def test_exact_duplicate_flags_only_later_copies():
    df = _df(_row(), _row(), _row(order_id="ORD-2"))
    assert v.exact_duplicate(df).tolist() == [False, True, False]


def test_duplicate_key_flags_same_key_with_different_content():
    df = _df(_row(), _row(quantity=5, revenue=50.0, profit=38.0), _row(line_number=2))
    assert v.duplicate_key(df).tolist() == [False, True, False]


def test_duplicate_key_ignores_blank_keys():
    df = _df(_row(order_id=np.nan), _row(order_id=np.nan))
    assert not v.duplicate_key(df).any()


def test_header_conflict_flags_the_minority_line_of_an_order():
    df = _df(_row(line_number=1), _row(line_number=2), _row(line_number=3, customer_id="C9"))
    assert v.header_conflict(df).tolist() == [False, False, True]


def test_header_conflict_clean_orders_are_not_flagged():
    df = _df(_row(), _row(order_id="ORD-2"), _row(order_id="ORD-2", line_number=2))
    assert not v.header_conflict(df).any()


# ------------------------------------------------------------------------- final gate
def test_validate_clean_returns_no_problems_for_a_valid_table():
    assert v.validate_clean(_df(_row(), _row(order_id="ORD-2"))).empty


def test_validate_clean_reports_each_broken_rule():
    df = _df(_row(),
             _row(order_id="O2", quantity=0),
             _row(order_id="O3", region="Mars"),
             _row(order_id="O4", revenue=99.0))
    problems = v.validate_clean(df).set_index("rule_id")
    assert {"R05", "R12", "R07"} <= set(problems.index)
    assert problems.loc["R05", "rows_affected"] == 1


def test_validate_clean_handles_an_empty_table():
    assert v.validate_clean(pd.DataFrame(columns=config.SALES_COLUMNS)).empty


def test_every_rule_in_the_catalogue_is_documented():
    for rule_id, rule in v.RULES.items():
        assert rule["name"] and rule["description"] and rule["decision"]
        assert rule["action"] in ("REPAIRED", "REMOVED")
