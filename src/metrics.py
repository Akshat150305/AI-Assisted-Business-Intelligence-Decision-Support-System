"""KPI calculations in pandas.

These are the SAME formulas as the SQL files in sql/. Running both and comparing the results
(tests/test_sql_python_agreement.py) is how the project proves its numbers are reliable.

Definitions (docs/requirements.md, section 8). All inputs are CLEAN order lines, one row per line.
    revenue           = sum of revenue
    profit            = sum of profit  (profit per line = revenue - cost)
    profit margin %   = profit / revenue * 100
    distinct orders   = number of distinct order_id        <- orders, NOT rows
    average order val = revenue / distinct orders
    repeat rate       = customers with >= 2 distinct orders / active customers
    % change          = (current - previous) / ABS(previous) * 100
A ratio whose denominator is zero (or missing) is None, never an error and never 0.

ALL DATA IS SYNTHETIC.
"""
import math

import pandas as pd

from src.filters import NO_FILTER, SalesFilter

KPI_NAMES = [
    "revenue", "cost", "profit", "profit_margin_pct", "distinct_orders", "order_lines",
    "units_sold", "avg_order_value", "active_customers", "revenue_per_customer",
    "repeat_customer_rate",
]
# KPIs shown in a period comparison (a subset of KPI_NAMES; repeat rate is not compared).
COMPARISON_KPIS = [
    "revenue", "cost", "profit", "profit_margin_pct", "distinct_orders", "order_lines",
    "units_sold", "avg_order_value", "active_customers",
]


# ----------------------------------------------------------------------------- small helpers
def _is_missing(x):
    return x is None or (isinstance(x, float) and math.isnan(x))


def safe_divide(numerator, denominator):
    """numerator / denominator, or None when the denominator is zero or missing."""
    if _is_missing(numerator) or _is_missing(denominator) or denominator == 0:
        return None
    return numerator / denominator


def pct_change(current, previous):
    """(current - previous) / ABS(previous) * 100, or None when previous is zero or missing.

    ABS() in the denominator keeps the sign meaningful when the previous value is negative
    (for example profit moving from -100 to -50 is an improvement of +50%).
    """
    if _is_missing(current) or _is_missing(previous) or previous == 0:
        return None
    return (current - previous) * 100.0 / abs(previous)


def _filtered(df, flt):
    return (flt or NO_FILTER).apply(df)


# ----------------------------------------------------------------------------- headline KPIs
def compute_kpis(df, flt=None):
    """All headline KPIs for the (filtered) data as a dict. Empty data gives zeros and None ratios."""
    f = _filtered(df, flt)
    revenue = float(f["revenue"].sum())
    cost = float(f["cost"].sum())
    profit = float(f["profit"].sum())
    orders = int(f["order_id"].nunique())
    customers = int(f["customer_id"].nunique())
    orders_per_customer = f.groupby("customer_id")["order_id"].nunique()
    repeat_customers = int((orders_per_customer >= 2).sum())
    return {
        "revenue": revenue,
        "cost": cost,
        "profit": profit,
        "profit_margin_pct": safe_divide(profit * 100.0, revenue),
        "distinct_orders": orders,
        "order_lines": int(len(f)),
        "units_sold": int(f["quantity"].sum()),
        "avg_order_value": safe_divide(revenue, orders),
        "active_customers": customers,
        "revenue_per_customer": safe_divide(revenue, customers),
        "repeat_customer_rate": safe_divide(repeat_customers, customers),
    }


# ----------------------------------------------------------------------------- aggregation helper
def _aggregate(groups):
    """The standard money/order measures for a groupby object."""
    out = groups.agg(
        revenue=("revenue", "sum"), cost=("cost", "sum"), profit=("profit", "sum"),
        distinct_orders=("order_id", "nunique"), order_lines=("order_id", "size"),
        units_sold=("quantity", "sum"),
    ).reset_index()
    out["profit_margin_pct"] = (out["profit"] * 100.0 / out["revenue"]).where(out["revenue"] != 0)
    out["avg_order_value"] = out["revenue"] / out["distinct_orders"]
    return out


MEASURE_ORDER = ["revenue", "cost", "profit", "profit_margin_pct", "distinct_orders",
                 "order_lines", "units_sold", "avg_order_value"]


# ----------------------------------------------------------------------------- breakdowns
BREAKDOWN_KEYS = {
    "region": ["region"],
    "category": ["category"],
    "customer_segment": ["customer_segment"],
    "product": ["product_id", "product_name", "category"],
}


def breakdown(df, by, flt=None):
    """Revenue, profit, margin, orders and shares by region / category / customer_segment / product.

    revenue_share_pct and profit_share_pct are shares of the total of the SAME filtered selection.
    Sorted by revenue (largest first), ties by key.
    """
    if by not in BREAKDOWN_KEYS:
        raise ValueError(f"by must be one of {sorted(BREAKDOWN_KEYS)}, got {by!r}")
    keys = BREAKDOWN_KEYS[by]
    f = _filtered(df, flt)
    columns = keys + MEASURE_ORDER + ["revenue_share_pct", "profit_share_pct"]
    if f.empty:
        return pd.DataFrame(columns=columns)
    out = _aggregate(f.groupby(keys))
    total_revenue, total_profit = out["revenue"].sum(), out["profit"].sum()
    out["revenue_share_pct"] = (out["revenue"] * 100.0 / total_revenue) if total_revenue != 0 else float("nan")
    out["profit_share_pct"] = (out["profit"] * 100.0 / total_profit) if total_profit != 0 else float("nan")
    out = out.sort_values(["revenue", keys[0]], ascending=[False, True], kind="stable").reset_index(drop=True)
    return out[columns]


def _month_columns(f):
    month = f["order_date"].dt.strftime("%Y-%m")
    index = f["order_date"].dt.year * 12 + f["order_date"].dt.month
    return month, index


def monthly_trend(df, flt=None):
    """Monthly measures plus month-over-month % change (only when the previous CALENDAR month exists)."""
    f = _filtered(df, flt)
    columns = ["month"] + MEASURE_ORDER + ["revenue_change_pct", "profit_change_pct"]
    if f.empty:
        return pd.DataFrame(columns=columns)
    month, index = _month_columns(f)
    work = f.assign(month=month, month_index=index)
    out = _aggregate(work.groupby(["month", "month_index"])).sort_values("month_index").reset_index(drop=True)
    adjacent = out["month_index"].diff() == 1
    for source, target in (("revenue", "revenue_change_pct"), ("profit", "profit_change_pct")):
        previous = out[source].shift(1)
        change = (out[source] - previous) * 100.0 / previous.abs()
        out[target] = change.where(adjacent & (previous != 0))
    return out[columns]


def region_monthly(df, flt=None):
    """Revenue, profit and margin by region and month."""
    f = _filtered(df, flt)
    columns = ["region", "month", "revenue", "cost", "profit", "profit_margin_pct",
               "distinct_orders", "avg_order_value"]
    if f.empty:
        return pd.DataFrame(columns=columns)
    out = _aggregate(f.assign(month=f["order_date"].dt.strftime("%Y-%m")).groupby(["region", "month"]))
    return out.sort_values(["region", "month"]).reset_index(drop=True)[columns]


# ----------------------------------------------------------------------------- customers
def customer_metrics(df, flt=None):
    """One row per customer (inside the filters), largest revenue first."""
    f = _filtered(df, flt)
    columns = ["customer_id", "customer_segment", "region", "distinct_orders", "order_lines",
               "units_sold", "revenue", "profit", "profit_margin_pct", "avg_order_value",
               "first_order_date", "last_order_date", "is_repeat_customer"]
    if f.empty:
        return pd.DataFrame(columns=columns)
    groups = f.groupby(["customer_id", "customer_segment", "region"])
    out = _aggregate(groups)
    dates = groups["order_date"].agg(first_order_date="min", last_order_date="max").reset_index()
    out = out.merge(dates, on=["customer_id", "customer_segment", "region"])
    out["first_order_date"] = out["first_order_date"].dt.strftime("%Y-%m-%d")
    out["last_order_date"] = out["last_order_date"].dt.strftime("%Y-%m-%d")
    out["is_repeat_customer"] = (out["distinct_orders"] >= 2).astype(int)
    out = out.sort_values(["revenue", "customer_id"], ascending=[False, True], kind="stable").reset_index(drop=True)
    return out[columns]


def top_n_customer_share(customers, n):
    """Share (%) of total revenue earned by the n biggest customers. None when revenue is zero."""
    if n < 0:
        raise ValueError("n must not be negative")
    total = customers["revenue"].sum()
    top = customers.sort_values("revenue", ascending=False)["revenue"].head(n).sum()
    return safe_divide(float(top) * 100.0, float(total))


def top_pct_customer_share(customers, pct):
    """Share (%) of revenue earned by the top ``pct`` percent of customers (rounded up to whole customers)."""
    if not 0 <= pct <= 100:
        raise ValueError("pct must be between 0 and 100")
    return top_n_customer_share(customers, math.ceil(len(customers) * pct / 100.0))


# ----------------------------------------------------------------------------- period comparison
def previous_period(start, end):
    """The period of the same length immediately before [start, end] (both inclusive)."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if start > end:
        raise ValueError("start is after end")
    days = (end - start).days + 1
    prev_end = start - pd.Timedelta(days=1)
    prev_start = prev_end - pd.Timedelta(days=days - 1)
    return prev_start.strftime("%Y-%m-%d"), prev_end.strftime("%Y-%m-%d")


def resolve_periods(cur_start, cur_end, prev_start=None, prev_end=None):
    """Validate the two periods and fill in the default previous period. Returns 4 ISO strings."""
    cur_start, cur_end = pd.Timestamp(cur_start), pd.Timestamp(cur_end)
    if cur_start > cur_end:
        raise ValueError("current period: start is after end")
    if (prev_start is None) != (prev_end is None):
        raise ValueError("give both prev_start and prev_end, or neither")
    if prev_start is None:
        prev_start, prev_end = previous_period(cur_start, cur_end)
    prev_start, prev_end = pd.Timestamp(prev_start), pd.Timestamp(prev_end)
    if prev_start > prev_end:
        raise ValueError("previous period: start is after end")
    if prev_start <= cur_end and cur_start <= prev_end:
        raise ValueError("the current and previous periods overlap")
    fmt = "%Y-%m-%d"
    return (cur_start.strftime(fmt), cur_end.strftime(fmt), prev_start.strftime(fmt), prev_end.strftime(fmt))


def comparison_table(current, previous):
    """Long table: kpi, current, previous, absolute_change, pct_change (from two KPI dicts)."""
    rows = []
    for kpi in COMPARISON_KPIS:
        cur, prev = current[kpi], previous[kpi]
        absolute = None if _is_missing(cur) or _is_missing(prev) else cur - prev
        rows.append({"kpi": kpi, "current": cur, "previous": prev,
                     "absolute_change": absolute, "pct_change": pct_change(cur, prev)})
    return pd.DataFrame(rows)


def compare_periods(df, cur_start, cur_end, prev_start=None, prev_end=None, flt=None):
    """Compare KPIs of a current period with a previous one (default: same length, directly before).

    ``flt`` may restrict region / category / customer segment, but not dates: the dates are the
    two periods themselves.
    """
    flt = flt or NO_FILTER
    if flt.has_dates:
        raise ValueError("flt must not contain dates; the periods define the dates")
    c_start, c_end, p_start, p_end = resolve_periods(cur_start, cur_end, prev_start, prev_end)
    base = SalesFilter(region=flt.region, category=flt.category, customer_segment=flt.customer_segment)
    current = compute_kpis(df, SalesFilter(c_start, c_end, base.region, base.category, base.customer_segment))
    previous = compute_kpis(df, SalesFilter(p_start, p_end, base.region, base.category, base.customer_segment))
    return comparison_table(current, previous)
