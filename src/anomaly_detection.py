"""Explainable anomaly detection: rolling, prior-only baseline on WEEKLY KPIs.

Method (in two sentences)
-------------------------
For every weekly series (revenue, profit or profit margin %, for the whole business and for each
region, category and customer segment) the baseline of a week is the MEDIAN of the previous N
complete weeks, and its deviation is (observed - baseline) / (1.4826 x MAD), where MAD is the median
absolute deviation of those same previous weeks (a "robust z-score"). A week is flagged when
|deviation| >= the threshold, as unusually HIGH or LOW. N and the threshold are in app_config/config.py
(currently 13 weeks and 4.5; the reasoning is in reports/anomaly_evaluation.md).

Design rules (docs/requirements.md FR-16 to FR-20)
--------------------------------------------------
* PRIOR DATA ONLY. The baseline of week t uses weeks t-N .. t-1 (N = window). Nothing from week t or later is
  used. tests/test_anomaly_detection.py proves it by deleting or changing the future and checking
  that every earlier result stays identical.
* WEEKLY GRAIN. Daily numbers are too noisy and have a strong weekday pattern. Weeks run Monday to
  Sunday. Only COMPLETE weeks (all 7 days inside the data's date range) are used, so a part-week at
  the end of the data is never mistaken for a sales collapse.
* ROBUST STATISTICS. Median and MAD are not distorted by one earlier spike, unlike mean and standard
  deviation, so a past anomaly does not hide the next one.
* INSUFFICIENT HISTORY. Fewer than ``min_history_weeks`` valid previous weeks: status
  "insufficient_history", never flagged.
* ZERO VARIANCE. If the previous weeks are all identical (MAD = 0) the deviation is undefined:
  status "zero_variance", never flagged (documented limitation, no division by zero).
* NO LABELS. This module receives only the sales table. It never reads the planted anomaly labels.
  Scoring against them lives in src/anomaly_evaluation.py.

WHAT A FLAG MEANS
-----------------
A flag is a STATISTICAL ANOMALY: this week is unusual compared with the previous N weeks. It is NOT a
confirmed business issue and says nothing about the cause. It does not predict anything. In
particular, the baseline cannot anticipate known seasonal peaks (for example November), so those
weeks can be flagged even though nothing is wrong. See reports/anomaly_evaluation.md.

ALL DATA IS SYNTHETIC.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app_config import config

MAD_SCALE = 1.4826      # makes MAD comparable to a standard deviation when data are roughly normal

# scope_type -> column of the sales table (None = the whole business)
SCOPE_COLUMNS = {
    "all": None,
    "region": "region",
    "category": "category",
    "customer_segment": "customer_segment",
}

OUTPUT_COLUMNS = [
    "scope_type", "scope_value", "metric", "week_start", "week_end", "observed", "baseline",
    "spread", "expected_low", "expected_high", "deviation", "threshold", "status",
    "is_anomaly", "direction", "severity", "suggested_check",
]

# What an analyst should CHECK. These are questions to investigate, not causes.
SUGGESTED_CHECKS = {
    ("revenue", "high"): "Look for a one-off bulk order, a promotion or duplicated data in this slice and week "
                         "before treating it as real demand.",
    ("revenue", "low"): "Look for a data-load gap, an outage or stock-out, or a genuine demand drop in this "
                        "slice and week.",
    ("profit", "high"): "Check whether the extra profit comes from higher volume, a price change or a "
                        "cost-data error.",
    ("profit", "low"): "Check whether lower profit comes from lower volume, higher unit costs or discounting.",
    ("profit_margin_pct", "high"): "Check for a cost-data error or a shift toward high-margin products.",
    ("profit_margin_pct", "low"): "Check for rising unit costs, discounting or a shift toward low-margin products.",
}


@dataclass(frozen=True)
class DetectionParams:
    window_weeks: int = config.ANOMALY_WINDOW_WEEKS
    min_history_weeks: int = config.ANOMALY_MIN_HISTORY_WEEKS
    threshold: float = config.ANOMALY_THRESHOLD
    severity_bands: tuple = config.ANOMALY_SEVERITY_BANDS

    def __post_init__(self):
        if self.window_weeks < 2:
            raise ValueError("window_weeks must be at least 2")
        if not 2 <= self.min_history_weeks <= self.window_weeks:
            raise ValueError("min_history_weeks must be between 2 and window_weeks")
        if self.threshold <= 0:
            raise ValueError("threshold must be positive")
        if len(self.severity_bands) != 2 or not self.severity_bands[0] < self.severity_bands[1]:
            raise ValueError("severity_bands must be two increasing numbers")


# ----------------------------------------------------------------------------- weekly aggregation
def complete_weeks(df):
    """Monday week-starts whose 7 days all lie inside [first order date, last order date]."""
    if df.empty:
        return pd.DatetimeIndex([])
    low, high = df["order_date"].min().normalize(), df["order_date"].max().normalize()
    first = low + pd.Timedelta(days=(7 - low.day_of_week) % 7)         # first Monday on or after low
    if first + pd.Timedelta(days=6) > high:
        return pd.DatetimeIndex([])
    n_weeks = ((high - pd.Timedelta(days=6) - first).days // 7) + 1
    return pd.date_range(first, periods=n_weeks, freq="7D")


def _aggregate(work, keys):
    out = work.groupby(keys).agg(revenue=("revenue", "sum"), profit=("profit", "sum"),
                                 distinct_orders=("order_id", "nunique")).reset_index()
    return out


def weekly_metrics(df):
    """Weekly revenue, profit, orders and margin for every scope. Weeks with no sales get zeros.

    Columns: scope_type, scope_value, week_start, revenue, profit, distinct_orders, profit_margin_pct
    """
    weeks = complete_weeks(df)
    columns = ["scope_type", "scope_value", "week_start", "revenue", "profit",
               "distinct_orders", "profit_margin_pct"]
    if len(weeks) == 0:
        return pd.DataFrame(columns=columns)
    day = df["order_date"].dt.normalize()
    work = df.assign(week_start=day - pd.to_timedelta(day.dt.day_of_week, unit="D"))
    work = work[work["week_start"].isin(weeks)]
    frames = []
    for scope_type, column in SCOPE_COLUMNS.items():
        if column is None:
            agg = _aggregate(work, ["week_start"]).assign(scope_value="All")
            values = ["All"]
        else:
            agg = _aggregate(work, [column, "week_start"]).rename(columns={column: "scope_value"})
            values = sorted(agg["scope_value"].unique())
        full = pd.MultiIndex.from_product([values, weeks], names=["scope_value", "week_start"])
        agg = (agg.set_index(["scope_value", "week_start"]).reindex(full)
               .fillna({"revenue": 0.0, "profit": 0.0, "distinct_orders": 0}).reset_index())
        agg["scope_type"] = scope_type
        frames.append(agg)
    out = pd.concat(frames, ignore_index=True)
    out["profit_margin_pct"] = (out["profit"] * 100.0 / out["revenue"]).where(out["revenue"] > 0)
    return out[columns]


# ----------------------------------------------------------------------------- scoring one series
def _mad(values):
    valid = values[~np.isnan(values)]
    if len(valid) == 0:
        return np.nan
    return float(np.median(np.abs(valid - np.median(valid))))


def score_series(values, params=None):
    """Score one weekly series. ``values`` is a float Series indexed by week (in time order).

    The baseline of position t is computed from ``values.shift(1)``, i.e. from earlier weeks only.
    Returns a DataFrame with observed, baseline, spread, expected_low/high, deviation, status, is_anomaly.
    """
    params = params or DetectionParams()
    observed = values.astype(float)
    prior = observed.shift(1)                         # <- the only place the time direction is decided
    window = prior.rolling(params.window_weeks, min_periods=params.min_history_weeks)
    baseline = window.median()
    spread = MAD_SCALE * window.apply(_mad, raw=True)

    status = pd.Series("evaluated", index=observed.index, dtype=object)
    status[spread == 0] = "zero_variance"
    status[baseline.isna() | spread.isna()] = "insufficient_history"
    status[observed.isna()] = "no_observation"
    ok = status == "evaluated"

    deviation = ((observed - baseline) / spread).where(ok)
    is_anomaly = ok & (deviation.abs() >= params.threshold)
    band = params.threshold * spread
    return pd.DataFrame({
        "observed": observed,
        "baseline": baseline,
        "spread": spread,
        "expected_low": (baseline - band).where(ok),
        "expected_high": (baseline + band).where(ok),
        "deviation": deviation,
        "status": status,
        "is_anomaly": is_anomaly,
    })


def _severity(deviation, params):
    moderate_to_high, high_to_extreme = params.severity_bands
    magnitude = deviation.abs()
    return pd.Series(np.select([magnitude >= high_to_extreme, magnitude >= moderate_to_high],
                               ["extreme", "high"], default="moderate"), index=deviation.index)


# ----------------------------------------------------------------------------- detection for every series
def detect(df, params=None, metrics=None):
    """Score every scope x metric x complete week. Returns one row per series-week (see OUTPUT_COLUMNS).

    ``df`` is the cleaned sales table. It is the ONLY input: there is deliberately no way to pass
    labels, thresholds per week or any knowledge of the future.
    """
    params = params or DetectionParams()
    metrics = metrics or config.ANOMALY_METRICS
    weekly = weekly_metrics(df)
    if weekly.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    results = []
    for (scope_type, scope_value), group in weekly.groupby(["scope_type", "scope_value"], sort=True):
        group = group.sort_values("week_start")
        for metric in metrics:
            scored = score_series(group.set_index("week_start")[metric], params)
            scored["scope_type"], scored["scope_value"], scored["metric"] = scope_type, scope_value, metric
            results.append(scored.reset_index())
    out = pd.concat(results, ignore_index=True)

    out["week_end"] = out["week_start"] + pd.Timedelta(days=6)
    out["threshold"] = params.threshold
    flagged = out["is_anomaly"]
    out["direction"] = np.where(flagged, np.where(out["deviation"] > 0, "high", "low"), None)
    out["severity"] = _severity(out["deviation"], params).where(flagged, None)
    out["suggested_check"] = [SUGGESTED_CHECKS.get((m, d)) if f else None
                              for m, d, f in zip(out["metric"], out["direction"], flagged)]
    out = out.sort_values(["scope_type", "scope_value", "metric", "week_start"]).reset_index(drop=True)
    return out[OUTPUT_COLUMNS]


def flags_only(detections):
    """The flagged rows, most extreme first."""
    flagged = detections[detections["is_anomaly"]].copy()
    return flagged.reindex(flagged["deviation"].abs().sort_values(ascending=False).index).reset_index(drop=True)


def export_weekly(detections, path=config.ANOMALY_WEEKLY_EXPORT):
    """Write every scored series-week (not just flags) so Power BI can draw actual vs expected."""
    path.parent.mkdir(parents=True, exist_ok=True)
    out = detections.copy()
    for column in ("week_start", "week_end"):
        out[column] = out[column].dt.strftime("%Y-%m-%d")
    out = out.round({"observed": 2, "baseline": 2, "spread": 2, "expected_low": 2,
                     "expected_high": 2, "deviation": 3})
    out.to_csv(path, index=False)
    return len(out)
