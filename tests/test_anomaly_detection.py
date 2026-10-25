"""Tests for the rolling-baseline anomaly detector.

The key promises, each with its own test:
  * the baseline uses PRIOR weeks only (no look-ahead),
  * too little history and zero variance are handled, never flagged, never crash,
  * partial weeks are never scored,
  * the detector cannot see the planted labels.
"""
import inspect

import numpy as np
import pandas as pd
import pytest

from app_config import config
from src import anomaly_detection as ad


def weekly_index(n):
    return pd.date_range("2025-01-06", periods=n, freq="7D")


def series(values):
    return pd.Series(values, index=weekly_index(len(values)), dtype=float)


PARAMS = ad.DetectionParams(window_weeks=8, min_history_weeks=6, threshold=3.5, severity_bands=(5.0, 8.0))


# ----------------------------------------------------------------------------- baseline arithmetic
def test_baseline_and_deviation_use_the_previous_weeks_only():
    values = [10, 12, 11, 13, 12, 11, 10, 12, 100]
    scored = ad.score_series(series(values), PARAMS)
    prior = np.array(values[:8], dtype=float)                          # weeks 0..7, NOT week 8 itself
    median = np.median(prior)
    mad = np.median(np.abs(prior - median))
    last = scored.iloc[-1]
    assert last["baseline"] == pytest.approx(median)
    assert last["spread"] == pytest.approx(1.4826 * mad)
    assert last["deviation"] == pytest.approx((100 - median) / (1.4826 * mad))
    assert last["status"] == "evaluated" and bool(last["is_anomaly"])


def test_the_week_being_scored_never_influences_its_own_baseline():
    base = [10, 12, 11, 13, 12, 11, 10, 12, 12]
    changed = base[:-1] + [500]
    a = ad.score_series(series(base), PARAMS).iloc[-1]
    b = ad.score_series(series(changed), PARAMS).iloc[-1]
    assert a["baseline"] == b["baseline"] and a["spread"] == b["spread"]


def test_expected_band_is_baseline_plus_or_minus_threshold_times_spread():
    scored = ad.score_series(series([10, 12, 11, 13, 12, 11, 10, 12, 12]), PARAMS).iloc[-1]
    assert scored["expected_low"] == pytest.approx(scored["baseline"] - 3.5 * scored["spread"])
    assert scored["expected_high"] == pytest.approx(scored["baseline"] + 3.5 * scored["spread"])


def test_scores_for_early_weeks_do_not_change_when_later_weeks_are_added():
    """Prefix property: scoring the first k weeks gives the same as the first k rows of the full series."""
    rng = np.random.default_rng(3)
    values = rng.normal(100, 6, 60)
    values[45] = 220
    full = ad.score_series(series(values), PARAMS)
    for k in (20, 33, 47):
        partial = ad.score_series(series(values[:k]), PARAMS)
        pd.testing.assert_frame_equal(partial, full.iloc[:k])


# ----------------------------------------------------------------------------- flagging
def test_unusually_high_and_low_weeks_are_flagged_in_the_right_direction():
    rng = np.random.default_rng(11)
    values = rng.normal(100, 5, 50)
    values[30], values[40] = 200.0, 20.0
    scored = ad.score_series(series(values), PARAMS)
    assert bool(scored.iloc[30]["is_anomaly"]) and scored.iloc[30]["deviation"] > 0
    assert bool(scored.iloc[40]["is_anomaly"]) and scored.iloc[40]["deviation"] < 0


def test_an_earlier_spike_does_not_hide_the_next_one():
    """Median and MAD are robust: one old outlier must not inflate the spread and mask a new anomaly."""
    rng = np.random.default_rng(5)
    values = rng.normal(100, 4, 40)
    values[20] = 300.0
    values[24] = 250.0
    scored = ad.score_series(series(values), PARAMS)
    assert bool(scored.iloc[20]["is_anomaly"]) and bool(scored.iloc[24]["is_anomaly"])


def test_threshold_is_inclusive_and_changing_it_changes_flags():
    values = [10, 12, 11, 13, 12, 11, 10, 12, 18]
    low = ad.score_series(series(values), ad.DetectionParams(8, 6, 3.0, (5.0, 8.0))).iloc[-1]
    high = ad.score_series(series(values), ad.DetectionParams(8, 6, 50.0, (60.0, 80.0))).iloc[-1]
    assert bool(low["is_anomaly"]) and not bool(high["is_anomaly"])
    assert low["deviation"] == high["deviation"]                       # same score, different decision


# ----------------------------------------------------------------------------- insufficient history, zero variance, missing
def test_insufficient_history_is_reported_and_never_flagged():
    scored = ad.score_series(series([10, 12, 11, 13, 12, 500, 10, 12]), PARAMS)
    assert (scored.iloc[:6]["status"] == "insufficient_history").all()      # fewer than 6 earlier weeks
    assert not scored.iloc[:6]["is_anomaly"].any()
    assert scored.iloc[:6]["deviation"].isna().all()
    assert scored.iloc[6]["status"] == "evaluated"


def test_zero_variance_history_is_reported_not_flagged_and_does_not_crash():
    scored = ad.score_series(series([100.0] * 10 + [150.0]), PARAMS)
    last = scored.iloc[-1]
    assert last["status"] == "zero_variance"
    assert pd.isna(last["deviation"]) and not bool(last["is_anomaly"])
    assert np.isfinite(scored["observed"]).all()


def test_missing_observations_and_gaps_in_history_are_handled():
    values = [10, 12, 11, 13, 12, 11, 10, 12, 11, 12]
    values[9] = np.nan
    scored = ad.score_series(series(values), PARAMS)
    assert scored.iloc[9]["status"] == "no_observation" and not bool(scored.iloc[9]["is_anomaly"])
    holes = [10, np.nan, 11, np.nan, 12, np.nan, 10, np.nan, 11]      # only 5 valid earlier values
    assert ad.score_series(series(holes), PARAMS).iloc[-1]["status"] == "insufficient_history"


def test_series_shorter_than_the_history_requirement_never_flags():
    scored = ad.score_series(series([1, 2, 3]), PARAMS)
    assert (scored["status"] == "insufficient_history").all() and not scored["is_anomaly"].any()


def test_parameter_validation():
    for bad in (dict(window_weeks=1), dict(min_history_weeks=1), dict(min_history_weeks=20, window_weeks=8),
                dict(threshold=0), dict(severity_bands=(8.0, 5.0)), dict(severity_bands=(1.0,))):
        with pytest.raises(ValueError):
            ad.DetectionParams(**bad)


# ----------------------------------------------------------------------------- weekly grain and complete weeks
def _sales(dates, region="North", revenue=100.0):
    return pd.DataFrame({
        "order_id": [f"O{i}" for i in range(len(dates))], "order_date": pd.to_datetime(dates),
        "region": region, "category": "Clothing", "customer_segment": "Consumer",
        "revenue": revenue, "profit": revenue * 0.4,
    })


def test_only_complete_monday_to_sunday_weeks_are_used():
    df = _sales(pd.date_range("2025-01-01", "2025-01-21"))             # Wed 1 Jan .. Tue 21 Jan
    weeks = ad.complete_weeks(df)
    assert [w.strftime("%Y-%m-%d") for w in weeks] == ["2025-01-06", "2025-01-13"]
    assert all(w.day_of_week == 0 for w in weeks)


def test_data_shorter_than_one_complete_week_gives_no_weeks_and_no_crash():
    df = _sales(pd.date_range("2025-01-01", "2025-01-05"))
    assert len(ad.complete_weeks(df)) == 0
    assert ad.weekly_metrics(df).empty
    assert list(ad.detect(df).columns) == ad.OUTPUT_COLUMNS and ad.detect(df).empty


def test_empty_input_does_not_crash():
    empty = _sales(pd.date_range("2025-01-01", periods=1)).iloc[0:0]
    assert ad.detect(empty).empty and ad.complete_weeks(empty).empty


def test_weeks_without_sales_become_zero_revenue_and_empty_margin():
    north = _sales(pd.date_range("2025-01-06", "2025-01-26"), "North")
    south = _sales(["2025-01-07", "2025-01-21"], "South")               # nothing in the middle week
    weekly = ad.weekly_metrics(pd.concat([north, south], ignore_index=True))
    middle = weekly[(weekly["scope_type"] == "region") & (weekly["scope_value"] == "South")
                    & (weekly["week_start"] == "2025-01-13")].iloc[0]
    assert middle["revenue"] == 0 and middle["profit"] == 0
    assert pd.isna(middle["profit_margin_pct"])


def test_weekly_totals_add_up_to_the_sales_in_complete_weeks(clean_df):
    weekly = ad.weekly_metrics(clean_df)
    complete = clean_df[clean_df["order_date"].between("2024-01-01", "2025-12-28")]
    for scope in ("all", "region", "category", "customer_segment"):
        part = weekly[weekly["scope_type"] == scope]
        assert part["revenue"].sum() == pytest.approx(complete["revenue"].sum(), rel=1e-9)
        assert part["profit"].sum() == pytest.approx(complete["profit"].sum(), rel=1e-9)


def test_the_partial_last_week_of_the_real_data_is_not_scored(clean_df):
    assert clean_df["order_date"].max().day_name() == "Wednesday"
    detections = ad.detect(clean_df)
    assert detections["week_end"].max() == pd.Timestamp("2025-12-28")
    assert detections["week_start"].max() == pd.Timestamp("2025-12-22")


# ----------------------------------------------------------------------------- output on the real data
def test_output_schema_and_series_count(clean_df):
    d = ad.detect(clean_df)
    assert list(d.columns) == ad.OUTPUT_COLUMNS
    assert d.groupby(["scope_type", "scope_value", "metric"]).ngroups == 13 * 3
    assert set(d["status"]) <= {"evaluated", "insufficient_history", "zero_variance", "no_observation"}
    assert (d["threshold"] == config.ANOMALY_THRESHOLD).all()
    assert d["week_start"].dt.day_of_week.eq(0).all() and ((d["week_end"] - d["week_start"]).dt.days == 6).all()


def test_every_flag_reports_observed_baseline_deviation_threshold_and_a_consistent_direction(clean_df):
    d = ad.detect(clean_df)
    flags = d[d["is_anomaly"]]
    assert len(flags) > 0
    for column in ("observed", "baseline", "spread", "deviation", "threshold", "direction", "severity",
                   "expected_low", "expected_high", "suggested_check"):
        assert flags[column].notna().all(), column
    assert (flags["deviation"].abs() >= flags["threshold"]).all()
    high, low = flags[flags["direction"] == "high"], flags[flags["direction"] == "low"]
    assert (high["observed"] > high["expected_high"]).all() and (high["deviation"] > 0).all()
    assert (low["observed"] < low["expected_low"]).all() and (low["deviation"] < 0).all()
    assert set(flags["severity"]) <= {"moderate", "high", "extreme"}
    unflagged = d[~d["is_anomaly"]]
    assert unflagged[["direction", "severity", "suggested_check"]].isna().all().all()


def test_deviation_is_observed_minus_baseline_over_spread(clean_df):
    ev = ad.detect(clean_df).query("status == 'evaluated'")
    expected = (ev["observed"] - ev["baseline"]) / ev["spread"]
    assert np.allclose(ev["deviation"], expected)


def test_severity_follows_the_configured_bands(clean_df):
    flags = ad.flags_only(ad.detect(clean_df))
    low, high = config.ANOMALY_SEVERITY_BANDS
    magnitude = flags["deviation"].abs()
    assert (flags.loc[magnitude < low, "severity"] == "moderate").all()
    assert (flags.loc[(magnitude >= low) & (magnitude < high), "severity"] == "high").all()
    assert (flags.loc[magnitude >= high, "severity"] == "extreme").all()


def test_suggested_checks_are_questions_to_investigate_not_causes(clean_df):
    for text in ad.flags_only(ad.detect(clean_df))["suggested_check"].unique():
        assert text.startswith(("Look for", "Check")), text


def test_flags_only_is_sorted_by_strength(clean_df):
    flags = ad.flags_only(ad.detect(clean_df))
    assert flags["deviation"].abs().is_monotonic_decreasing


# ----------------------------------------------------------------------------- NO LOOK-AHEAD on the real data
CUTOFF = pd.Timestamp("2025-06-29")                                    # a Sunday, so no partial week


def _same_rows(a, b):
    key = ["scope_type", "scope_value", "metric", "week_start"]
    a = a.sort_values(key).reset_index(drop=True)
    b = b.sort_values(key).reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b)


def test_deleting_the_future_does_not_change_any_earlier_result(clean_df):
    full = ad.detect(clean_df)
    truncated = ad.detect(clean_df[clean_df["order_date"] <= CUTOFF])
    _same_rows(full[full["week_end"] <= CUTOFF], truncated)
    assert len(truncated) > 0


def test_distorting_the_future_does_not_change_any_earlier_result(clean_df):
    tampered = clean_df.copy()
    future = tampered["order_date"] > CUTOFF
    tampered.loc[future, ["revenue", "profit"]] = tampered.loc[future, ["revenue", "profit"]] * 25
    full, distorted = ad.detect(clean_df), ad.detect(tampered)
    _same_rows(full[full["week_end"] <= CUTOFF], distorted[distorted["week_end"] <= CUTOFF])
    assert not full[full["week_end"] > CUTOFF]["observed"].equals(distorted[distorted["week_end"] > CUTOFF]["observed"])


# ----------------------------------------------------------------------------- NO LABEL LEAKAGE
def test_detector_takes_only_the_sales_table_and_settings():
    assert list(inspect.signature(ad.detect).parameters) == ["df", "params", "metrics"]
    assert list(inspect.signature(ad.score_series).parameters) == ["values", "params"]


def _code_references(module):
    """Imported module names and every identifier used in the code (docstrings are ignored)."""
    import ast
    tree = ast.parse(inspect.getsource(module))
    imported, names = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported |= {node.module or ""} | {f"{node.module}.{alias.name}" for alias in node.names}
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return imported, names


def test_detector_code_never_imports_the_evaluation_module_or_touches_label_settings():
    imported, names = _code_references(ad)
    assert not any("anomaly_evaluation" in name for name in imported), imported
    for forbidden in ("ANOMALY_LABELS_FILE", "DEFECT_LABELS_FILE", "load_labels", "evaluate"):
        assert forbidden not in names, forbidden


def test_detector_has_no_label_column_in_its_vocabulary():
    for column in ad.OUTPUT_COLUMNS:
        assert "label" not in column and "planted" not in column and "anomaly_id" not in column


def test_detection_results_do_not_depend_on_the_label_file(clean_df, monkeypatch, tmp_path):
    before = ad.detect(clean_df)
    monkeypatch.setattr(config, "ANOMALY_LABELS_FILE", tmp_path / "does_not_exist.csv")
    after = ad.detect(clean_df)                                         # works with the label file missing
    pd.testing.assert_frame_equal(before, after)


# ----------------------------------------------------------------------------- export for Power BI
def test_export_writes_every_scored_week_not_only_flags(clean_df, tmp_path):
    detections = ad.detect(clean_df)
    path = tmp_path / "anomaly_weekly.csv"
    written = ad.export_weekly(detections, path)
    exported = pd.read_csv(path)
    assert written == len(exported) == len(detections)
    assert int(exported["is_anomaly"].sum()) == int(detections["is_anomaly"].sum())
    assert exported["week_start"].str.fullmatch(r"\d{4}-\d{2}-\d{2}").all()
    assert not any("label" in c.lower() or "planted" in c.lower() for c in exported.columns)
