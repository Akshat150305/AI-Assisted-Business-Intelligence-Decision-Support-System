"""SQL and Python must give the same numbers for the same filters.

Two layers of proof:
1. The tiny hand-calculated dataset: SQL gives the numbers worked out by hand.
2. The full cleaned data (50k lines): every SQL query is compared with its pandas twin
   across many filter combinations, including empty selections.
"""
import math

import pandas as pd
import pytest

from src import metrics, queries
from src.filters import SalesFilter

FILTERS = {
    "no filter": SalesFilter(),
    "one month": SalesFilter("2025-03-01", "2025-03-31"),
    "one day": SalesFilter("2025-06-10", "2025-06-10"),
    "open start": SalesFilter(end_date="2024-06-30"),
    "open end": SalesFilter(start_date="2025-07-01"),
    "region": SalesFilter(region="East"),
    "category": SalesFilter(category="Electronics"),
    "segment": SalesFilter(customer_segment="Corporate"),
    "combined": SalesFilter("2025-01-01", "2025-09-30", "West", "Clothing", "Small Business"),
    "empty: unknown region": SalesFilter(region="Mars"),
    "empty: before data": SalesFilter("2020-01-01", "2020-12-31"),
}
FILTER_IDS = list(FILTERS)


def close(a, b):
    if a is None or b is None or (isinstance(a, float) and math.isnan(a)) or (isinstance(b, float) and math.isnan(b)):
        return (a is None or pd.isna(a)) and (b is None or pd.isna(b))
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6)


def assert_frames_close(sql, py, keys):
    """Same rows (by key) and the same numbers in every shared column."""
    assert len(sql) == len(py), f"row counts differ: sql={len(sql)} python={len(py)}"
    assert set(sql.columns) == set(py.columns)
    if sql.empty:
        return
    s = sql.sort_values(keys).reset_index(drop=True)
    p = py.sort_values(keys).reset_index(drop=True)
    for col in sql.columns:
        for i, (a, b) in enumerate(zip(s[col], p[col])):
            if isinstance(a, str) or isinstance(b, str):
                assert a == b, f"{col}[{i}]: {a!r} != {b!r}"
            else:
                assert close(a, b), f"{col}[{i}]: sql={a} python={b}"


# ----------------------------------------------------------------------------- the hand calculation, in SQL
def test_sql_gives_the_hand_calculated_kpis(tiny_conn):
    k = queries.kpi_summary(tiny_conn)
    assert close(k["revenue"], 55) and close(k["cost"], 28) and close(k["profit"], 27)
    assert close(k["profit_margin_pct"], 27 / 55 * 100)
    assert k["distinct_orders"] == 3 and k["order_lines"] == 4 and k["units_sold"] == 8
    assert close(k["avg_order_value"], 55 / 3)
    assert k["active_customers"] == 2 and close(k["revenue_per_customer"], 27.5)
    assert close(k["repeat_customer_rate"], 0.5)


def test_sql_empty_selection_returns_zeros_and_null_ratios(tiny_conn):
    k = queries.kpi_summary(tiny_conn, SalesFilter(region="Mars"))
    assert k["revenue"] == 0 and k["distinct_orders"] == 0 and k["order_lines"] == 0
    assert k["profit_margin_pct"] is None and k["avg_order_value"] is None
    assert k["revenue_per_customer"] is None and k["repeat_customer_rate"] is None


def test_sql_does_not_compare_across_a_missing_month(tiny_df, tmp_path):
    from src import database
    gap = tiny_df.copy()
    gap.loc[gap["order_id"] == "C", "order_date"] = pd.Timestamp("2025-03-05")
    path = tmp_path / "gap.db"
    database.build_database(gap, path)
    conn = database.connect(path)
    try:
        t = queries.monthly_trend(conn).set_index("month")
    finally:
        conn.close()
    assert list(t.index) == ["2025-01", "2025-03"]
    assert pd.isna(t.loc["2025-03", "revenue_change_pct"])


def test_sql_monthly_change_matches_the_hand_calculation(tiny_conn):
    t = queries.monthly_trend(tiny_conn).set_index("month")
    assert close(t.loc["2025-02", "revenue_change_pct"], (20 - 35) / 35 * 100)
    assert close(t.loc["2025-02", "profit_change_pct"], (12 - 15) / 15 * 100)


# ----------------------------------------------------------------------------- full data: SQL == pandas
@pytest.mark.parametrize("name", FILTER_IDS)
def test_kpi_summary_agrees(conn, clean_df, name):
    flt = FILTERS[name]
    sql, py = queries.kpi_summary(conn, flt), metrics.compute_kpis(clean_df, flt)
    assert set(sql) == set(py) == set(metrics.KPI_NAMES)
    for kpi in metrics.KPI_NAMES:
        assert close(sql[kpi], py[kpi]), f"{kpi}: sql={sql[kpi]} python={py[kpi]}"


@pytest.mark.parametrize("name", FILTER_IDS)
def test_monthly_trend_agrees(conn, clean_df, name):
    flt = FILTERS[name]
    assert_frames_close(queries.monthly_trend(conn, flt), metrics.monthly_trend(clean_df, flt), ["month"])


@pytest.mark.parametrize("name", FILTER_IDS)
@pytest.mark.parametrize("by, sql_function, keys", [
    ("region", queries.by_region, ["region"]),
    ("category", queries.by_category, ["category"]),
    ("customer_segment", queries.by_segment, ["customer_segment"]),
    ("product", queries.by_product, ["product_id"]),
])
def test_breakdowns_agree(conn, clean_df, name, by, sql_function, keys):
    flt = FILTERS[name]
    assert_frames_close(sql_function(conn, flt), metrics.breakdown(clean_df, by, flt), keys)


@pytest.mark.parametrize("name", ["no filter", "one month", "region", "combined", "empty: unknown region"])
def test_region_monthly_and_customer_metrics_agree(conn, clean_df, name):
    flt = FILTERS[name]
    assert_frames_close(queries.region_monthly(conn, flt), metrics.region_monthly(clean_df, flt), ["region", "month"])
    assert_frames_close(queries.customer_metrics(conn, flt), metrics.customer_metrics(clean_df, flt), ["customer_id"])


PERIODS = {
    "default previous period (month)": ("2025-12-01", "2025-12-31", None, None, SalesFilter()),
    "default previous period (week)": ("2025-03-17", "2025-03-23", None, None, SalesFilter()),
    "explicit previous (year over year)": ("2025-01-01", "2025-06-30", "2024-01-01", "2024-06-30", SalesFilter()),
    "with region and category": ("2025-10-01", "2025-10-31", None, None, SalesFilter(region="West", category="Clothing")),
    "previous period before data": ("2024-01-01", "2024-01-31", None, None, SalesFilter()),
    "single day": ("2025-05-14", "2025-05-14", None, None, SalesFilter(customer_segment="Corporate")),
}


@pytest.mark.parametrize("name", list(PERIODS))
def test_period_comparison_agrees(conn, clean_df, name):
    cur_start, cur_end, prev_start, prev_end, flt = PERIODS[name]
    sql = queries.period_comparison(conn, cur_start, cur_end, prev_start, prev_end, flt)
    py = metrics.compare_periods(clean_df, cur_start, cur_end, prev_start, prev_end, flt)
    assert_frames_close(sql, py, ["kpi"])


def test_period_comparison_before_the_data_has_no_percentage_change(conn):
    table = queries.period_comparison(conn, "2024-01-01", "2024-01-31").set_index("kpi")
    assert table.loc["revenue", "previous"] == 0
    assert pd.isna(table.loc["revenue", "pct_change"])


def test_period_comparison_rejects_dates_in_the_filter(conn):
    with pytest.raises(ValueError, match="must not contain dates"):
        queries.period_comparison(conn, "2025-02-01", "2025-02-28", flt=SalesFilter(end_date="2025-03-01"))


def test_period_comparison_rejects_overlapping_periods(conn):
    with pytest.raises(ValueError, match="overlap"):
        queries.period_comparison(conn, "2025-02-01", "2025-02-28", "2025-02-20", "2025-03-05")


# ----------------------------------------------------------------------------- integrity of the numbers themselves
@pytest.mark.parametrize("name", ["no filter", "one month", "region", "combined"])
def test_breakdowns_add_up_to_the_headline_kpis(conn, name):
    flt = FILTERS[name]
    total = queries.kpi_summary(conn, flt)
    for table in (queries.by_region(conn, flt), queries.by_category(conn, flt),
                  queries.by_segment(conn, flt), queries.by_product(conn, flt),
                  queries.monthly_trend(conn, flt)):
        assert close(table["revenue"].sum(), total["revenue"])
        assert close(table["profit"].sum(), total["profit"])
        assert table["order_lines"].sum() == total["order_lines"]
    assert close(queries.by_product(conn, flt)["revenue_share_pct"].sum(), 100)


def test_orders_add_up_across_region_but_not_across_category(conn):
    """An order has one region, so region order counts add up. An order can span several
    categories, so category order counts over-count. Never add distinct counts across a dimension."""
    total = queries.kpi_summary(conn)["distinct_orders"]
    assert queries.by_region(conn)["distinct_orders"].sum() == total
    assert queries.by_segment(conn)["distinct_orders"].sum() == total
    assert queries.by_category(conn)["distinct_orders"].sum() > total


def test_profit_is_revenue_minus_cost_everywhere(conn):
    k = queries.kpi_summary(conn)
    assert close(k["profit"], k["revenue"] - k["cost"])
    for table in (queries.by_region(conn), queries.monthly_trend(conn), queries.customer_metrics(conn)):
        if "cost" in table:
            assert (table["profit"] - (table["revenue"] - table["cost"])).abs().max() < 1e-6


def test_average_order_value_uses_orders_not_lines(conn):
    k = queries.kpi_summary(conn)
    assert k["order_lines"] > k["distinct_orders"]
    assert close(k["avg_order_value"], k["revenue"] / k["distinct_orders"])
    assert not close(k["avg_order_value"], k["revenue"] / k["order_lines"])


def test_customer_concentration_is_consistent_between_sql_and_python(conn, clean_df):
    sql = queries.customer_metrics(conn)
    py = metrics.customer_metrics(clean_df)
    for n in (1, 10, 100):
        assert close(metrics.top_n_customer_share(sql, n), metrics.top_n_customer_share(py, n))
    assert close(metrics.top_n_customer_share(sql, len(sql)), 100)
    assert metrics.top_pct_customer_share(sql, 10) > 10      # revenue is concentrated, not evenly spread
