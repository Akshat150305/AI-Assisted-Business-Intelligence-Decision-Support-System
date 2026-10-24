"""Run the SQL files in sql/ and return pandas DataFrames.

Each public function = one SQL file. The SQL does the aggregation; this module only supplies the
parameters (from a SalesFilter) and tidies the result. No KPI is calculated here except the
period-over-period change, which uses the same pct_change() helper as src/metrics.py.

ALL DATA IS SYNTHETIC.
"""
import pandas as pd

from app_config import config
from src import metrics
from src.filters import NO_FILTER, SalesFilter

_SQL_CACHE = {}


def load_sql(name):
    """Text of sql/<name>.sql."""
    if name not in _SQL_CACHE:
        path = config.SQL_DIR / f"{name}.sql"
        if not path.exists():
            raise FileNotFoundError(f"SQL file not found: {path}")
        _SQL_CACHE[name] = path.read_text(encoding="utf-8")
    return _SQL_CACHE[name]


def run_query(conn, name, params):
    """Execute sql/<name>.sql with named parameters and return a DataFrame."""
    return pd.read_sql_query(load_sql(name), conn, params=params)


def _none_if_nan(value):
    return None if pd.isna(value) else value


def kpi_summary(conn, flt=None):
    """Headline KPIs as a dict (same keys as metrics.compute_kpis)."""
    row = run_query(conn, "kpi_summary", (flt or NO_FILTER).to_params()).iloc[0]
    out = {}
    for name in metrics.KPI_NAMES:
        value = _none_if_nan(row[name])
        if name in ("distinct_orders", "order_lines", "units_sold", "active_customers") and value is not None:
            value = int(value)
        out[name] = value
    return out


def monthly_trend(conn, flt=None):
    return run_query(conn, "monthly_trend", (flt or NO_FILTER).to_params())


def by_region(conn, flt=None):
    return run_query(conn, "by_region", (flt or NO_FILTER).to_params())


def by_category(conn, flt=None):
    return run_query(conn, "by_category", (flt or NO_FILTER).to_params())


def by_segment(conn, flt=None):
    return run_query(conn, "by_segment", (flt or NO_FILTER).to_params())


def by_product(conn, flt=None):
    return run_query(conn, "by_product", (flt or NO_FILTER).to_params())


def region_monthly(conn, flt=None):
    return run_query(conn, "region_monthly", (flt or NO_FILTER).to_params())


def customer_metrics(conn, flt=None):
    return run_query(conn, "customer_metrics", (flt or NO_FILTER).to_params())


def period_comparison(conn, cur_start, cur_end, prev_start=None, prev_end=None, flt=None):
    """Current vs previous period, as the same long table that metrics.compare_periods returns."""
    flt = flt or NO_FILTER
    if flt.has_dates:
        raise ValueError("flt must not contain dates; the periods define the dates")
    c_start, c_end, p_start, p_end = metrics.resolve_periods(cur_start, cur_end, prev_start, prev_end)
    params = {"cur_start": c_start, "cur_end": c_end, "prev_start": p_start, "prev_end": p_end,
              "region": flt.region, "category": flt.category, "segment": flt.customer_segment}
    row = run_query(conn, "period_comparison", params).iloc[0]

    def kpis(prefix):
        revenue, profit = float(row[f"{prefix}_revenue"]), float(row[f"{prefix}_profit"])
        orders, customers = int(row[f"{prefix}_distinct_orders"]), int(row[f"{prefix}_active_customers"])
        return {
            "revenue": revenue,
            "cost": float(row[f"{prefix}_cost"]),
            "profit": profit,
            "profit_margin_pct": metrics.safe_divide(profit * 100.0, revenue),
            "distinct_orders": orders,
            "order_lines": int(row[f"{prefix}_order_lines"]),
            "units_sold": int(row[f"{prefix}_units_sold"]),
            "avg_order_value": metrics.safe_divide(revenue, orders),
            "active_customers": customers,
        }

    return metrics.comparison_table(kpis("cur"), kpis("prev"))
