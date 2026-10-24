"""Python KPI tests against the tiny hand-calculated dataset (conftest.TINY_ROWS).

Hand calculation (order A has 2 lines, order B 1 line, order C 1 line):
    revenue  = 20 + 5 + 10 + 20            = 55
    cost     = 12 + 2 + 6 + 8              = 28
    profit   = 8 + 3 + 4 + 12              = 27
    margin   = 27 / 55 * 100               = 49.0909...
    orders   = 3 (A, B, C)    lines = 4    <- distinct orders, not rows
    AOV      = 55 / 3                      = 18.3333...   (NOT 55 / 4)
    units    = 2 + 1 + 1 + 4               = 8
    customers= 2 (C1, C2)     revenue per customer = 27.5
    repeat   = C1 has 2 orders, C2 has 1   -> 1 / 2 = 0.5
"""
import math

import pandas as pd
import pytest

from src import metrics
from src.filters import SalesFilter


def close(a, b):
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)


# ----------------------------------------------------------------------------- helpers
def test_safe_divide_guards_zero_and_missing():
    assert metrics.safe_divide(10, 4) == 2.5
    assert metrics.safe_divide(0, 5) == 0.0
    assert metrics.safe_divide(10, 0) is None
    assert metrics.safe_divide(10, None) is None
    assert metrics.safe_divide(float("nan"), 5) is None
    assert metrics.safe_divide(5, float("nan")) is None


def test_pct_change_basic_and_guards():
    assert close(metrics.pct_change(120, 100), 20.0)
    assert close(metrics.pct_change(80, 100), -20.0)
    assert metrics.pct_change(5, 0) is None
    assert metrics.pct_change(5, None) is None
    assert metrics.pct_change(None, 5) is None


def test_pct_change_keeps_a_meaningful_sign_when_previous_is_negative():
    # profit from -100 to -50 is an improvement, so the change must be positive
    assert close(metrics.pct_change(-50, -100), 50.0)
    assert close(metrics.pct_change(-150, -100), -50.0)


# ----------------------------------------------------------------------------- headline KPIs
def test_headline_kpis_match_the_hand_calculation(tiny_df):
    k = metrics.compute_kpis(tiny_df)
    assert close(k["revenue"], 55) and close(k["cost"], 28) and close(k["profit"], 27)
    assert close(k["profit_margin_pct"], 27 / 55 * 100)
    assert k["distinct_orders"] == 3 and k["order_lines"] == 4 and k["units_sold"] == 8
    assert close(k["avg_order_value"], 55 / 3)
    assert k["active_customers"] == 2 and close(k["revenue_per_customer"], 27.5)
    assert close(k["repeat_customer_rate"], 0.5)


def test_orders_are_counted_distinctly_not_by_row(tiny_df):
    k = metrics.compute_kpis(tiny_df)
    assert k["order_lines"] > k["distinct_orders"]
    assert not math.isclose(k["avg_order_value"], k["revenue"] / k["order_lines"])


def test_profit_equals_revenue_minus_cost(tiny_df):
    k = metrics.compute_kpis(tiny_df)
    assert close(k["profit"], k["revenue"] - k["cost"])


def test_kpis_respect_filters(tiny_df):
    jan = metrics.compute_kpis(tiny_df, SalesFilter("2025-01-01", "2025-01-31"))
    assert close(jan["revenue"], 35) and jan["distinct_orders"] == 2 and jan["order_lines"] == 3
    north_clothing = metrics.compute_kpis(tiny_df, SalesFilter(region="North", category="Clothing"))
    assert close(north_clothing["revenue"], 5) and north_clothing["distinct_orders"] == 1


def test_empty_selection_gives_zeros_and_none_ratios_without_error(tiny_df):
    k = metrics.compute_kpis(tiny_df, SalesFilter(region="Mars"))
    assert k["revenue"] == 0 and k["profit"] == 0 and k["distinct_orders"] == 0
    assert k["profit_margin_pct"] is None and k["avg_order_value"] is None
    assert k["revenue_per_customer"] is None and k["repeat_customer_rate"] is None


def test_empty_dataframe_does_not_crash(tiny_df):
    empty = tiny_df.iloc[0:0]
    k = metrics.compute_kpis(empty)
    assert k["revenue"] == 0 and k["profit_margin_pct"] is None
    assert metrics.monthly_trend(empty).empty
    assert metrics.breakdown(empty, "region").empty
    assert metrics.customer_metrics(empty).empty
    assert metrics.region_monthly(empty).empty


# ----------------------------------------------------------------------------- monthly trend
def test_monthly_trend_and_month_over_month_change(tiny_df):
    t = metrics.monthly_trend(tiny_df).set_index("month")
    assert close(t.loc["2025-01", "revenue"], 35) and close(t.loc["2025-02", "revenue"], 20)
    assert t.loc["2025-01", "distinct_orders"] == 2 and t.loc["2025-01", "order_lines"] == 3
    assert pd.isna(t.loc["2025-01", "revenue_change_pct"])               # no previous month
    assert close(t.loc["2025-02", "revenue_change_pct"], (20 - 35) / 35 * 100)
    assert close(t.loc["2025-02", "profit_change_pct"], (12 - 15) / 15 * 100)


def test_month_over_month_change_is_not_calculated_across_a_missing_month(tiny_df):
    gap = tiny_df.copy()
    gap.loc[gap["order_id"] == "C", "order_date"] = pd.Timestamp("2025-03-05")   # January then March
    t = metrics.monthly_trend(gap).set_index("month")
    assert list(t.index) == ["2025-01", "2025-03"]
    assert pd.isna(t.loc["2025-03", "revenue_change_pct"])


# ----------------------------------------------------------------------------- breakdowns
def test_breakdown_by_region_values_and_shares(tiny_df):
    b = metrics.breakdown(tiny_df, "region").set_index("region")
    assert close(b.loc["North", "revenue"], 35) and close(b.loc["South", "revenue"], 20)
    assert close(b.loc["North", "revenue_share_pct"], 35 / 55 * 100)
    assert close(b["revenue_share_pct"].sum(), 100) and close(b["profit_share_pct"].sum(), 100)
    assert metrics.breakdown(tiny_df, "region")["region"].tolist() == ["North", "South"]   # largest first


def test_breakdown_by_product_keeps_product_name_and_category(tiny_df):
    b = metrics.breakdown(tiny_df, "product")
    assert {"product_id", "product_name", "category"} <= set(b.columns)
    assert close(b.set_index("product_id").loc["P1", "revenue"], 30)


def test_breakdown_rejects_an_unknown_dimension(tiny_df):
    with pytest.raises(ValueError, match="by must be one of"):
        metrics.breakdown(tiny_df, "colour")


def test_distinct_orders_are_not_additive_across_categories(tiny_df):
    """Order A contains Electronics AND Clothing, so summing category order counts over-counts."""
    by_category = metrics.breakdown(tiny_df, "category")
    assert by_category["distinct_orders"].sum() > metrics.compute_kpis(tiny_df)["distinct_orders"]


# ----------------------------------------------------------------------------- customers
def test_customer_metrics_and_repeat_flag(tiny_df):
    c = metrics.customer_metrics(tiny_df).set_index("customer_id")
    assert c.loc["C1", "distinct_orders"] == 2 and c.loc["C1", "is_repeat_customer"] == 1
    assert c.loc["C2", "distinct_orders"] == 1 and c.loc["C2", "is_repeat_customer"] == 0
    assert c.loc["C1", "first_order_date"] == "2025-01-10" and c.loc["C1", "last_order_date"] == "2025-01-20"
    assert close(c.loc["C1", "avg_order_value"], 35 / 2)


def test_top_n_customer_share(tiny_df):
    c = metrics.customer_metrics(tiny_df)
    assert close(metrics.top_n_customer_share(c, 1), 35 / 55 * 100)
    assert close(metrics.top_n_customer_share(c, 2), 100)
    assert close(metrics.top_n_customer_share(c, 99), 100)
    assert metrics.top_n_customer_share(c, 0) == 0
    assert metrics.top_n_customer_share(c.iloc[0:0], 3) is None
    assert close(metrics.top_pct_customer_share(c, 50), 35 / 55 * 100)    # top 50% of 2 customers = 1 customer
    with pytest.raises(ValueError):
        metrics.top_pct_customer_share(c, 101)


# ----------------------------------------------------------------------------- period comparison
def test_previous_period_has_the_same_length_and_sits_directly_before():
    assert metrics.previous_period("2025-02-01", "2025-02-28") == ("2025-01-04", "2025-01-31")
    assert metrics.previous_period("2025-03-10", "2025-03-10") == ("2025-03-09", "2025-03-09")


def test_resolve_periods_validates_input():
    with pytest.raises(ValueError, match="overlap"):
        metrics.resolve_periods("2025-02-01", "2025-02-28", "2025-02-15", "2025-03-10")
    with pytest.raises(ValueError, match="both prev_start and prev_end"):
        metrics.resolve_periods("2025-02-01", "2025-02-28", "2025-01-01", None)
    with pytest.raises(ValueError, match="after end"):
        metrics.resolve_periods("2025-02-28", "2025-02-01")


def test_compare_periods_values(tiny_df):
    table = metrics.compare_periods(tiny_df, "2025-02-01", "2025-02-28").set_index("kpi")
    # previous period = 2025-01-04 .. 2025-01-31 = orders A and B
    assert close(table.loc["revenue", "current"], 20) and close(table.loc["revenue", "previous"], 35)
    assert close(table.loc["revenue", "absolute_change"], -15)
    assert close(table.loc["revenue", "pct_change"], -15 / 35 * 100)
    assert table.loc["distinct_orders", "previous"] == 2


def test_compare_periods_with_an_empty_previous_period_gives_no_percentage(tiny_df):
    table = metrics.compare_periods(tiny_df, "2025-01-01", "2025-01-31").set_index("kpi")   # previous = December 2024
    assert table.loc["revenue", "previous"] == 0
    assert pd.isna(table.loc["revenue", "pct_change"])
    assert pd.isna(table.loc["profit_margin_pct", "previous"])


def test_compare_periods_rejects_dates_inside_the_filter(tiny_df):
    with pytest.raises(ValueError, match="must not contain dates"):
        metrics.compare_periods(tiny_df, "2025-02-01", "2025-02-28", flt=SalesFilter(start_date="2025-01-01"))
