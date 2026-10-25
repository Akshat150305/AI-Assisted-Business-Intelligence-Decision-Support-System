"""Tests for scoring the detector against the planted labels (src/anomaly_evaluation.py)."""
import pandas as pd
import pytest

from app_config import config
from src import anomaly_detection as ad
from src import anomaly_evaluation as ev


# ----------------------------------------------------------------------------- hand-made situation
def label(anomaly_id="A1", anomaly_type="revenue_spike", start="2025-03-17", end="2025-03-23",
          region="South", category="", metric="revenue"):
    return {"anomaly_id": anomaly_id, "anomaly_type": anomaly_type, "start_date": pd.Timestamp(start),
            "end_date": pd.Timestamp(end), "region": region, "category": category, "affected_metric": metric}


def detection(scope_type, scope_value, metric, week, direction, flagged=True, deviation=9.0):
    week = pd.Timestamp(week)
    return {"scope_type": scope_type, "scope_value": scope_value, "metric": metric, "week_start": week,
            "week_end": week + pd.Timedelta(days=6), "deviation": deviation if direction == "high" else -deviation,
            "direction": direction if flagged else None, "is_anomaly": flagged, "status": "evaluated"}


def frame(*rows):
    return pd.DataFrame(list(rows))


def test_scope_overlap_rules():
    south = ("region", "South")
    assert ev.scopes_overlap(south, ("region", "South"))
    assert not ev.scopes_overlap(south, ("region", "West"))                 # a different region is unrelated
    assert ev.scopes_overlap(south, ("all", "All"))                         # the total includes South
    assert ev.scopes_overlap(south, ("category", "Clothing"))               # a category includes South sales
    assert ev.scopes_overlap(("all", "All"), ("region", "West"))            # a whole-business event touches everything
    assert not ev.scopes_overlap(("category", "Electronics"), ("category", "Clothing"))


def test_label_scope_and_window_helpers():
    assert ev.label_scope(label(region="South")) == ("region", "South")
    assert ev.label_scope(label(region="", category="Electronics")) == ("category", "Electronics")
    assert ev.label_scope(label(region="", category="")) == ("all", "All")
    lab = label(start="2025-03-19", end="2025-03-19")                       # a Wednesday inside the week of the 17th
    assert ev.overlaps_window(pd.Timestamp("2025-03-17"), pd.Timestamp("2025-03-23"), lab)
    assert not ev.overlaps_window(pd.Timestamp("2025-03-24"), pd.Timestamp("2025-03-30"), lab)


def test_event_detected_only_when_the_primary_series_flags_in_the_right_direction():
    labels = pd.DataFrame([label()])
    right = frame(detection("region", "South", "revenue", "2025-03-17", "high"))
    wrong_direction = frame(detection("region", "South", "revenue", "2025-03-17", "low"))
    wrong_series = frame(detection("region", "West", "revenue", "2025-03-17", "high"))
    wrong_metric = frame(detection("region", "South", "profit", "2025-03-17", "high"))
    wrong_week = frame(detection("region", "South", "revenue", "2025-04-14", "high"))
    assert ev.evaluate(right, labels)["per_event"]["detected"].iloc[0]
    for miss in (wrong_direction, wrong_series, wrong_metric, wrong_week):
        assert not ev.evaluate(miss, labels)["per_event"]["detected"].iloc[0]


def test_flags_are_split_into_explained_and_unexplained():
    labels = pd.DataFrame([label()])
    detections = frame(
        detection("region", "South", "revenue", "2025-03-17", "high"),       # primary -> explained
        detection("all", "All", "revenue", "2025-03-17", "high"),            # total includes South -> explained
        detection("category", "Clothing", "profit", "2025-03-17", "high"),   # overlapping slice -> explained
        detection("region", "West", "revenue", "2025-03-17", "high"),        # different region -> unexplained
        detection("region", "South", "revenue", "2025-05-05", "high"),       # outside the window -> unexplained
        detection("region", "South", "revenue", "2025-03-24", "high", flagged=False),   # not a flag at all
    )
    result = ev.evaluate(detections, labels)
    s = result["summary"]
    assert s["flags"] == 5 and s["flags_explained"] == 3 and s["flags_unexplained"] == 2
    assert s["events_detected"] == 1 and s["events"] == 1
    unexplained = result["flags"][result["flags"]["explained_by"].isna()]
    assert set(zip(unexplained["scope_value"], unexplained["week_start"].dt.strftime("%Y-%m-%d"))) == {
        ("West", "2025-03-17"), ("South", "2025-05-05")}


def test_no_flags_means_no_event_detected_and_no_division_error():
    labels = pd.DataFrame([label()])
    none = frame(detection("region", "South", "revenue", "2025-03-17", "high", flagged=False))
    result = ev.evaluate(none, labels)
    assert result["summary"]["events_detected"] == 0 and result["summary"]["flags"] == 0
    assert pd.isna(result["per_event"]["max_abs_deviation"].iloc[0])


# ----------------------------------------------------------------------------- the planted events in the real pipeline
@pytest.fixture(scope="module")
def real(clean_df, datasets, tmp_path_factory):
    labels = datasets["anomaly_labels"].copy()
    labels["start_date"] = pd.to_datetime(labels["start_date"])
    labels["end_date"] = pd.to_datetime(labels["end_date"])
    labels = labels.fillna("")
    detections = ad.detect(clean_df)
    return {"labels": labels, "detections": detections, "result": ev.evaluate(detections, labels)}


def test_label_file_roundtrip_matches_the_generator(datasets, tmp_path):
    path = tmp_path / "labels.csv"
    datasets["anomaly_labels"].to_csv(path, index=False)
    loaded = ev.load_labels(path)
    assert list(loaded["anomaly_id"]) == ["A1", "A2", "A3", "A4"]
    assert loaded["start_date"].dtype.kind == "M" and loaded["region"].tolist()[2] == ""


def test_all_four_planted_events_are_detected_with_the_documented_parameters(real):
    per_event = real["result"]["per_event"].set_index("anomaly_id")
    assert per_event["detected"].all(), per_event[["primary_series", "detected"]]
    assert per_event.loc["A1", "expected_direction"] == "high" and per_event.loc["A2", "expected_direction"] == "low"
    assert per_event.loc["A3", "primary_series"] == "Electronics / profit_margin_pct"
    assert (per_event["max_abs_deviation"] >= config.ANOMALY_THRESHOLD).all()


def test_the_margin_squeeze_is_flagged_in_each_of_its_four_weeks(real):
    a3 = real["result"]["per_event"].set_index("anomaly_id").loc["A3"]
    assert a3["weeks_flagged"] == a3["weeks_in_window"] == 4


def test_most_flags_are_not_planted_events_so_the_evaluation_reports_false_alarms_honestly(real):
    s = real["result"]["summary"]
    assert s["flags"] == s["flags_explained"] + s["flags_unexplained"]
    assert s["flags_unexplained"] > 0                      # a rolling baseline is never perfect
    assert s["unexplained_rate_pct"] < 3.0                 # but false alarms stay rare


# ----------------------------------------------------------------------------- calibration and sensitivity
def test_noise_calibration_shows_why_the_first_parameters_were_replaced():
    table = ev.null_calibration({
        "first": ad.DetectionParams(8, 6, 3.5, (5.0, 8.0)),
        "in use": ad.DetectionParams(),
    }, n_series=60).set_index("parameters")
    assert table.loc["first", "false_alarm_rate_pct"] > 3.0        # roughly 1 week in 20 on pure noise
    assert table.loc["in use", "false_alarm_rate_pct"] < 1.5       # about 1% on pure noise


def test_noise_calibration_is_reproducible():
    one = ev.null_calibration({"x": ad.DetectionParams()}, n_series=20)
    two = ev.null_calibration({"x": ad.DetectionParams()}, n_series=20)
    pd.testing.assert_frame_equal(one, two)


def test_a_higher_threshold_never_flags_more(clean_df, real):
    table = ev.sensitivity(clean_df, real["labels"], thresholds=(3.5, 4.5, 6.0, 8.0))
    assert table["flags"].is_monotonic_decreasing
    assert table["unexplained_flags"].is_monotonic_decreasing
    row_in_use = table[table["threshold"] == 4.5].iloc[0]
    assert row_in_use["flags"] == real["result"]["summary"]["flags"]


# ----------------------------------------------------------------------------- report
def test_report_is_written_from_the_run_and_states_its_limits(clean_df, real, tmp_path):
    path = tmp_path / "report.md"
    detections, result = ev.run(clean_df, labels=real["labels"], report_path=path)
    text = path.read_text(encoding="utf-8")
    s = result["summary"]
    assert "Synthetic data" in text and "**not** a confirmed business issue" in text
    assert "Parameter history" in text and "Limitations" in text and "Sensitivity" in text
    assert f"{s['events_detected']} of {s['events']} planted events detected" in text
    assert f"| Flags | {s['flags']:,} " in text
    assert "anecdote" in text                                        # small-sample warning is present
    pd.testing.assert_frame_equal(detections, real["detections"])    # run() uses the same detector output
