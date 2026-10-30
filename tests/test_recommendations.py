"""Tests for the rule-based findings engine (src/recommendations.py).

Most tests use a tiny synthetic data set built here, where the correct answer is known in advance
(for example "revenue exactly +10% in the last quarter"), so each rule is tested at, above and below its threshold.
"""
import ast
import inspect

import pandas as pd
import pytest

from app_config import config
from src import anomaly_detection as ad
from src import metrics, recommendations as rec
from src.filters import SalesFilter

QUARTERS = pd.period_range("2024Q1", "2025Q4", freq="Q")
REGIONS = ["North", "South"]
PRODUCTS = {"P1": ("Alpha", "Cat A"), "P2": ("Beta", "Cat A"), "P3": ("Gamma", "Cat B")}
BASE = rec.FindingParams(high_confidence_orders=50, medium_confidence_orders=20)     # tiny data -> small order counts


def build(spec=None, quarters=QUARTERS, orders=10):
    """One order per row. spec(quarter, region, product_id) -> (revenue per order, margin %). Default 100, 40%."""
    spec = spec or (lambda q, r, p: (100.0, 40.0))
    rows, n = [], 0
    for q in quarters:
        dates = pd.date_range(q.start_time.normalize(), q.end_time.normalize(), periods=orders).normalize()
        for region in REGIONS:
            for pid, (name, category) in PRODUCTS.items():
                revenue, margin = spec(q, region, pid)
                for d in dates:
                    n += 1
                    cost = revenue * (1 - margin / 100.0)
                    rows.append({"order_id": f"O{n:06d}", "line_number": 1, "order_date": d, "customer_id": f"C{n % 40:03d}",
                                 "customer_segment": "Consumer", "product_id": pid, "product_name": name, "category": category,
                                 "region": region, "quantity": 1, "unit_price": revenue, "revenue": revenue, "cost": cost,
                                 "profit": revenue - cost})
    return pd.DataFrame(rows)


def run(df, detections=None, params=BASE):
    return rec.generate_findings(df, detections, params)


def ids(findings):
    return set(findings["finding_id"])


# ----------------------------------------------------------------------------- windows
def test_complete_quarters_ignore_partial_quarters():
    df = build(quarters=pd.period_range("2024Q1", "2024Q4", freq="Q"))
    df = df[df["order_date"] <= "2024-11-30"]                       # Q4 is cut short
    assert [str(q) for q in rec.complete_quarters(df)] == ["2024Q1", "2024Q2", "2024Q3"]


def test_analysis_windows_for_two_years_of_data():
    w = rec.analysis_windows(build())
    assert w["latest_q"] == ("2025-10-01", "2025-12-31") and w["prev_q"] == ("2025-07-01", "2025-09-30")
    assert w["same_q_ly"] == ("2024-10-01", "2024-12-31") and w["prev_q_ly"] == ("2024-07-01", "2024-09-30")
    assert w["current"] == ("2025-01-01", "2025-12-31") and w["prior"] == ("2024-01-01", "2024-12-31")
    assert w["latest_q_name"] == "2025-Q4" and w["n_quarters"] == 4


def test_windows_shrink_gracefully_with_little_data():
    w = rec.analysis_windows(build(quarters=pd.period_range("2025Q1", "2025Q2", freq="Q")))
    assert w["current"] == ("2025-01-01", "2025-06-30") and w["prior"] is None and w["same_q_ly"] is None
    one = rec.analysis_windows(build(quarters=pd.period_range("2025Q1", "2025Q1", freq="Q")))
    assert one["prev_q"] is None and one["current"] == ("2025-01-01", "2025-03-31")


def test_params_are_validated():
    with pytest.raises(ValueError):
        rec.FindingParams(material_change_pct=0)
    with pytest.raises(ValueError):
        rec.FindingParams(high_confidence_orders=10, medium_confidence_orders=20)
    with pytest.raises(ValueError):
        rec.FindingParams(anomaly_lookback_weeks=0)


# ----------------------------------------------------------------------------- empty / degenerate data
def test_empty_data_gives_an_empty_table_with_the_right_columns():
    result = run(build().iloc[0:0])
    assert result.empty and list(result.columns) == rec.FINDING_COLUMNS


def test_less_than_one_complete_quarter_gives_no_findings():
    df = build(quarters=pd.period_range("2025Q1", "2025Q1", freq="Q"))
    assert run(df[df["order_date"] < "2025-03-01"]).empty


def test_flat_data_yields_only_the_margin_status_finding():
    result = run(build())
    assert list(result["finding_type"]) == ["status"] and result.iloc[0]["priority_score"] == 0.0
    assert "holding" in result.iloc[0]["title"]


def test_a_zero_baseline_does_not_crash_or_invent_a_percentage():
    df = build()
    df = df[~((df["order_date"] >= "2025-07-01") & (df["order_date"] <= "2025-09-30"))]   # previous quarter has no sales
    result = run(df)                                                     # must not raise
    assert not any(result["rule"] == "QOQ")


# ----------------------------------------------------------------------------- quarter-on-quarter rule
def last_quarter_spec(factor, margin=40.0):
    return lambda q, r, p: (round(100.0 * factor, 6) if q == pd.Period("2025Q4") else 100.0, margin)   # round: 100*1.1 is 110.00000000000001


def test_quarter_change_fires_exactly_at_the_threshold():
    result = run(build(last_quarter_spec(1.10)))
    finding = result[result["finding_id"] == "F-QOQ-revenue-2025-Q4"].iloc[0]
    assert finding["pct_change"] == 10.0 and finding["observed_value"] == pytest.approx(6600.0)
    assert finding["baseline_value"] == pytest.approx(6000.0) and finding["absolute_change"] == pytest.approx(600.0)


def test_quarter_change_is_silent_below_the_threshold():
    assert "QOQ" not in set(run(build(last_quarter_spec(1.09)))["rule"])


def test_a_fall_is_reported_as_a_fall():
    finding = run(build(last_quarter_spec(0.80))).query("rule == 'QOQ' and metric == 'revenue'").iloc[0]
    assert finding["pct_change"] == pytest.approx(-20.0) and "fell" in finding["title"]


def test_profit_impact_of_a_revenue_change_uses_the_baseline_margin():
    finding = run(build(last_quarter_spec(1.10))).query("rule == 'QOQ' and metric == 'revenue'").iloc[0]
    assert finding["profit_impact"] == pytest.approx(600.0 * 0.40)


def test_a_repeated_seasonal_move_lowers_confidence_and_says_so():
    one_off = run(build(last_quarter_spec(1.30))).query("rule == 'QOQ' and metric == 'revenue'").iloc[0]
    seasonal = run(build(lambda q, r, p: (130.0 if q.quarter == 4 else 100.0, 40.0))).query("rule == 'QOQ' and metric == 'revenue'").iloc[0]
    assert one_off["confidence"] == "High" and seasonal["confidence"] == "Medium"
    assert "seasonal" in " ".join(seasonal["evidence"]).lower() and "earlier" in seasonal["limitations"]
    assert "not simply a repeat" in " ".join(one_off["evidence"])


def test_without_a_prior_year_the_seasonality_caveat_is_stated():
    df = build(lambda q, r, p: (130.0 if q == pd.Period("2025Q2") else 100.0, 40.0), quarters=pd.period_range("2025Q1", "2025Q2", freq="Q"))
    finding = run(df).query("rule == 'QOQ' and metric == 'revenue'").iloc[0]
    assert "seasonality cannot be checked" in finding["limitations"]


# ----------------------------------------------------------------------------- year-on-year and margin rules
def grown_2025(factor, margin_2024=40.0, margin_2025=40.0):
    return lambda q, r, p: ((100.0 * factor, margin_2025) if q.year == 2025 else (100.0, margin_2024))


def test_year_change_reports_the_growth_with_drivers():
    result = run(build(grown_2025(1.20)))
    f = result[result["finding_id"] == "F-YOY-revenue"].iloc[0]
    assert f["pct_change"] == pytest.approx(20.0) and f["observed_value"] == pytest.approx(28800.0) and f["baseline_value"] == pytest.approx(24000.0)
    text = " ".join(f["evidence"])
    assert "Distinct orders" in text and "Largest category contributor" in text and "Largest region contributor" in text
    assert "profit" in " ".join(result["metric"])                         # profit grew by the same 20%


def test_year_change_needs_a_prior_window():
    df = build(lambda q, r, p: (200.0 if q == pd.Period("2025Q2") else 100.0, 40.0), quarters=pd.period_range("2025Q1", "2025Q2", freq="Q"))
    assert "YOY" not in set(run(df)["rule"])


def test_margin_shift_above_the_threshold_is_a_change_finding():
    result = run(build(grown_2025(1.0, margin_2024=40.0, margin_2025=43.0)))
    f = result[result["finding_id"] == "F-MARGIN-YOY-year-on-year"].iloc[0]
    assert f["finding_type"] == "change" and "improved" in f["title"] and f["absolute_change"] == pytest.approx(3.0)
    assert "status" not in set(result["finding_type"])


def test_margin_shift_below_the_threshold_is_only_a_status_finding():
    result = run(build(grown_2025(1.0, margin_2024=40.0, margin_2025=41.0)))
    assert "F-MARGIN-YOY-stable" in ids(result) and "F-MARGIN-YOY-year-on-year" not in ids(result)


def test_margin_weakening_is_reported_as_weakened():
    f = run(build(grown_2025(1.0, margin_2024=40.0, margin_2025=36.0))).query("rule == 'MARGIN-YOY' and finding_type == 'change'").iloc[0]
    assert "weakened" in f["title"]


# ----------------------------------------------------------------------------- category and product rules
def category_spec(margin_a, margin_b, revenue_b=100.0):
    return lambda q, r, p: ((100.0, margin_a) if PRODUCTS[p][1] == "Cat A" else (revenue_b, margin_b))


def test_weak_and_strong_categories_are_found_against_the_business_margin():
    result = run(build(category_spec(margin_a=50.0, margin_b=20.0)))     # business margin = (2x50 + 1x20)/3 = 40%
    weak = result[result["finding_id"] == "F-CATMARGIN-Cat-B"].iloc[0]
    strong = result[result["finding_id"] == "F-CATMARGIN-Cat-A"].iloc[0]
    assert weak["finding_type"] == "weakness" and strong["finding_type"] == "strength"
    assert weak["observed_value"] == pytest.approx(20.0) and weak["baseline_value"] == pytest.approx(40.0)
    assert weak["profit_impact"] == pytest.approx(20.0 * 8000.0 / 100.0)          # gap (pp) x Cat B revenue of 2025 / 100


def test_category_gap_just_below_the_threshold_is_ignored():
    # business margin 40%: Cat A 44%, Cat B 32%  -> gaps 4 and 8 ; only B fires (threshold 5)
    result = run(build(category_spec(margin_a=44.0, margin_b=32.0)))
    assert "F-CATMARGIN-Cat-B" in ids(result) and "F-CATMARGIN-Cat-A" not in ids(result)


def test_tiny_categories_are_ignored_whatever_their_margin():
    result = run(build(category_spec(margin_a=40.0, margin_b=5.0, revenue_b=1.0)))     # Cat B is ~0.5% of revenue
    assert not any(result["rule"] == "CATMARGIN")


def test_a_product_far_from_its_own_category_is_found():
    spec = lambda q, r, p: (100.0, 25.0 if p == "P2" else 40.0)
    result = run(build(spec), params=rec.FindingParams(high_confidence_orders=50, medium_confidence_orders=20, category_gap_pp=50.0))
    finding = result[result["finding_id"] == "F-PRODUCT-Beta"].iloc[0]
    assert finding["finding_type"] == "weakness" and finding["baseline_value"] == pytest.approx((25 + 40) / 2)
    assert result.set_index("finding_id").loc["F-PRODUCT-Alpha", "finding_type"] == "strength" and "F-PRODUCT-Gamma" not in ids(result)


# ----------------------------------------------------------------------------- regional rules
def test_a_lagging_region_is_found_with_the_arithmetic_of_the_gap():
    spec = lambda q, r, p: (100.0 * (1.30 if (q.year == 2025 and r == "North") else 1.0), 40.0)
    result = run(build(spec))
    north = result[result["finding_id"] == "F-REGGROWTH-North"].iloc[0]
    south = result[result["finding_id"] == "F-REGGROWTH-South"].iloc[0]
    assert north["pct_change"] == pytest.approx(30.0) and "above" in north["title"]
    assert south["pct_change"] == pytest.approx(0.0) and "below" in south["title"]
    company = (1.30 + 1.0) / 2 * 100 - 100                                    # 15% overall
    assert south["profit_impact"] == pytest.approx(abs(south["observed_value"] - south["baseline_value"] * (1 + company / 100)) * 0.40, rel=1e-6)


def test_region_margin_gap_needs_the_threshold():
    spec = lambda q, r, p: (100.0, 30.0 if r == "South" else 40.0)           # business 35%: both regions 5 pp away
    assert {"F-REGMARGIN-North", "F-REGMARGIN-South"} <= ids(run(build(spec)))
    near = lambda q, r, p: (100.0, 38.0 if r == "South" else 40.0)            # 1 pp away
    assert not any(run(build(near))["rule"] == "REGMARGIN")


# ----------------------------------------------------------------------------- anomaly rule (hand-made detections)
def flag(scope_type, scope_value, metric, week, direction, severity, observed, baseline, deviation=9.0):
    week = pd.Timestamp(week)
    return {"scope_type": scope_type, "scope_value": scope_value, "metric": metric, "week_start": week,
            "week_end": week + pd.Timedelta(days=6), "observed": observed, "baseline": baseline, "spread": 10.0,
            "expected_low": baseline - 45, "expected_high": baseline + 45, "deviation": deviation if direction == "high" else -deviation,
            "threshold": 4.5, "status": "evaluated", "is_anomaly": True, "direction": direction, "severity": severity,
            "suggested_check": "Look for a one-off bulk order."}


def detections_frame(*rows):
    last = pd.Timestamp("2025-12-22")
    filler = {**flag("all", "All", "revenue", last, "high", "moderate", 100, 100), "is_anomaly": False, "direction": None, "severity": None}
    return pd.DataFrame([filler, *rows])


def test_recent_high_and_extreme_flags_become_findings_and_others_do_not():
    det = detections_frame(
        flag("region", "North", "revenue", "2025-11-10", "high", "extreme", 5000, 3000, 12.0),
        flag("region", "South", "profit", "2025-11-17", "low", "high", 1000, 1800, -8.5),
        flag("region", "South", "revenue", "2025-11-24", "high", "moderate", 4000, 3000, 6.0),     # moderate: ignored
        flag("region", "North", "profit", "2025-01-06", "high", "extreme", 4000, 1000, 15.0))      # too old: ignored
    result = run(build(), det)
    anomalies = result[result["rule"] == "ANOMALY"].set_index("finding_id")
    assert set(anomalies.index) == {"F-ANOMALY-North-revenue", "F-ANOMALY-South-profit"}
    north, south = anomalies.loc["F-ANOMALY-North-revenue"], anomalies.loc["F-ANOMALY-South-profit"]
    assert north["profit_impact"] == pytest.approx(2000 * 0.40)           # revenue gap x business margin
    assert south["profit_impact"] == pytest.approx(800.0)                 # profit gap as is
    assert north["confidence"] == "Medium" and south["confidence"] == "Low"
    assert "not a confirmed issue" in north["recommended_action"] and "unusually low" in south["title"]


def test_one_finding_per_series_using_its_largest_deviation():
    det = detections_frame(flag("region", "North", "revenue", "2025-11-10", "high", "high", 4000, 3000, 6.0),
                           flag("region", "North", "revenue", "2025-11-17", "high", "extreme", 6000, 3000, 14.0))
    f = run(build(), det).query("rule == 'ANOMALY'")
    assert len(f) == 1 and "2025-11-17" in f.iloc[0]["period"] and "2 flagged week" in " ".join(f.iloc[0]["evidence"])


def test_no_detections_means_no_anomaly_findings_and_no_error():
    assert not any(run(build(), None)["rule"] == "ANOMALY")
    assert not any(run(build(), detections_frame())["rule"] == "ANOMALY")


# ----------------------------------------------------------------------------- structure of every finding (FR-22, FR-23, FR-24)
@pytest.fixture(scope="module")
def busy():
    spec = lambda q, r, p: ((130.0 if (q.year == 2025 and r == "North") else 100.0) * (1.2 if q == pd.Period("2025Q4") else 1.0),
                            20.0 if PRODUCTS[p][1] == "Cat B" else 50.0 if p == "P1" else 44.0)
    return run(build(spec))


def test_every_finding_has_every_required_field(busy):
    assert len(busy) >= 6
    for _, f in busy.iterrows():
        for field in ("finding_id", "finding_type", "title", "metric", "scope", "period", "baseline_period", "confidence",
                      "observed_fact", "possible_explanation", "recommended_action", "evidence_needed", "limitations"):
            assert isinstance(f[field], str) and f[field].strip(), (f["finding_id"], field)
        assert f["evidence"] and all(isinstance(e, str) and e for e in f["evidence"])
        assert f["confidence"] in rec.CONFIDENCE_ORDER and f["finding_type"] in rec.FINDING_TYPES
        assert "Synthetic" in f["limitations"]


def test_fact_explanation_action_and_evidence_needed_are_kept_separate(busy):
    for _, f in busy.iterrows():
        assert len({f["observed_fact"], f["possible_explanation"], f["recommended_action"], f["evidence_needed"]}) == 4
        assert f["finding_type"] == "status" or "cause" in f["possible_explanation"].lower() or "possible" in f["possible_explanation"].lower()


def test_explanations_are_hypotheses_and_actions_make_no_promises(busy):
    forbidden = ("will increase", "guarantee", "will improve", "will boost", "proves", "because of")
    for _, f in busy.iterrows():
        text = (f["possible_explanation"] + " " + f["recommended_action"]).lower()
        assert not any(word in text for word in forbidden), f["finding_id"]


def test_ids_are_unique_and_ranks_are_contiguous_and_follow_priority(busy):
    assert busy["finding_id"].is_unique
    assert list(busy["rank"]) == list(range(1, len(busy) + 1))
    assert busy["priority_score"].is_monotonic_decreasing


def test_ties_are_broken_by_confidence_then_id():
    result = run(build(category_spec(margin_a=50.0, margin_b=20.0)))
    tied = result[result["priority_score"].duplicated(keep=False)]
    for _, group in tied.groupby("priority_score"):
        order = list(zip(group["confidence"].map(rec.CONFIDENCE_ORDER), group["finding_id"]))
        assert order == sorted(order)


def test_priority_is_profit_at_stake_as_a_share_of_current_window_profit():
    df = build(last_quarter_spec(1.10))
    f = run(df).query("rule == 'QOQ' and metric == 'profit'").iloc[0]
    window_profit = metrics.compute_kpis(df, SalesFilter(start_date="2025-01-01", end_date="2025-12-31"))["profit"]
    assert f["priority_score"] == pytest.approx(round(100 * f["profit_impact"] / window_profit, 2))


def test_confidence_follows_the_order_counts():
    assert rec.confidence_from_orders(50, BASE) == "High" and rec.confidence_from_orders(49, BASE) == "Medium"
    assert rec.confidence_from_orders(20, BASE) == "Medium" and rec.confidence_from_orders(19, BASE) == "Low"
    assert rec.downgrade("High") == "Medium" and rec.downgrade("Low") == "Low"


# ----------------------------------------------------------------------------- no hard-coded results
def test_numbers_come_from_the_data_not_from_the_code():
    base = run(build(grown_2025(1.20))).set_index("finding_id")
    doubled = build(grown_2025(1.20))
    doubled[["revenue", "cost", "profit", "unit_price"]] *= 2
    scaled = run(doubled).set_index("finding_id")
    assert scaled.loc["F-YOY-revenue", "observed_value"] == pytest.approx(2 * base.loc["F-YOY-revenue", "observed_value"])
    assert scaled.loc["F-YOY-revenue", "pct_change"] == pytest.approx(base.loc["F-YOY-revenue", "pct_change"])
    assert f"{scaled.loc['F-YOY-revenue', 'observed_value']:,.0f}" in scaled.loc["F-YOY-revenue", "observed_fact"]
    assert f"{base.loc['F-YOY-revenue', 'observed_value']:,.0f}" not in scaled.loc["F-YOY-revenue", "observed_fact"]


def test_changing_a_threshold_changes_the_findings():
    df = build(last_quarter_spec(1.15))
    assert "QOQ" in set(run(df, params=rec.FindingParams(material_change_pct=10, high_confidence_orders=50, medium_confidence_orders=20))["rule"])
    assert "QOQ" not in set(run(df, params=rec.FindingParams(material_change_pct=20, high_confidence_orders=50, medium_confidence_orders=20))["rule"])


def test_the_engine_is_deterministic():
    pd.testing.assert_frame_equal(run(build(grown_2025(1.2))), run(build(grown_2025(1.2))))


def test_the_engine_never_touches_the_planted_labels():
    tree = ast.parse(inspect.getsource(rec))
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {(n.module or "") + "." + a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert not any("evaluation" in name or "data_generator" in name for name in imported), imported
    source = inspect.getsource(rec)
    assert "injected" not in source and "anomaly_labels" not in source


# ----------------------------------------------------------------------------- export and report
def test_export_joins_evidence_and_round_trips(busy, tmp_path):
    path = tmp_path / "findings.csv"
    assert rec.export_findings(busy, path) == len(busy)
    loaded = pd.read_csv(path)
    assert list(loaded.columns) == rec.FINDING_COLUMNS and list(loaded["finding_id"]) == list(busy["finding_id"])
    assert " | " in loaded["evidence"].iloc[0] or len(busy.iloc[0]["evidence"]) == 1


def test_report_states_limits_thresholds_and_the_top_findings(busy):
    text = rec.build_report(busy, rec.analysis_windows(build()), BASE, top_n=3)
    assert "Synthetic data" in text and "hypotheses" in text and "Rules and thresholds" in text and "Limitations" in text
    assert busy.iloc[0]["title"] in text and busy.iloc[2]["title"] in text and busy.iloc[3]["title"] not in text.split("## All findings")[0]
    assert f"{rec.FindingParams().material_change_pct:g}%" in text


def test_report_handles_no_findings():
    text = rec.build_report(rec.empty_findings(), rec.analysis_windows(build().iloc[0:0]))
    assert "No finding reached a threshold" in text


# ----------------------------------------------------------------------------- the real pipeline data
@pytest.fixture(scope="module")
def real(clean_df):
    detections = ad.detect(clean_df)
    return {"findings": rec.generate_findings(clean_df, detections), "detections": detections}


def test_real_data_findings_trace_back_to_kpis(clean_df, real):
    f = real["findings"].set_index("finding_id")
    yoy = metrics.compare_periods(clean_df, "2025-01-01", "2025-12-31", "2024-01-01", "2024-12-31").set_index("kpi")
    assert f.loc["F-YOY-revenue", "observed_value"] == pytest.approx(yoy.loc["revenue", "current"])
    assert f.loc["F-YOY-revenue", "pct_change"] == pytest.approx(yoy.loc["revenue", "pct_change"])
    electronics = metrics.breakdown(clean_df, "category", SalesFilter(start_date="2025-01-01", end_date="2025-12-31")).set_index("category")
    assert f.loc["F-CATMARGIN-Electronics", "observed_value"] == pytest.approx(electronics.loc["Electronics", "profit_margin_pct"])


def test_real_data_structure_is_valid_and_covers_the_requested_topics(real):
    findings = real["findings"]
    assert findings["finding_id"].is_unique and list(findings["rank"]) == list(range(1, len(findings) + 1))
    assert {"change", "weakness", "strength", "regional"} <= set(findings["finding_type"])
    assert findings["priority_score"].is_monotonic_decreasing and (findings["priority_score"] >= 0).all()


def test_real_data_anomaly_findings_come_from_recent_high_severity_flags(real):
    det, findings = real["detections"], real["findings"]
    anomalies = findings[findings["rule"] == "ANOMALY"]
    recent = det[det["is_anomaly"] & det["severity"].isin(["high", "extreme"]) &
                 (det["week_start"] > det["week_start"].max() - pd.Timedelta(weeks=config.FINDING_ANOMALY_LOOKBACK_WEEKS))]
    assert len(anomalies) == recent.groupby(["scope_type", "scope_value", "metric"]).ngroups
    assert (anomalies["confidence"] != "High").all()


def test_run_writes_the_export_and_the_report(clean_df, real, tmp_path):
    findings = rec.run(clean_df, real["detections"], export_path=tmp_path / "f.csv", report_path=tmp_path / "r.md")
    pd.testing.assert_frame_equal(findings, real["findings"])
    assert len(pd.read_csv(tmp_path / "f.csv")) == len(findings)
    assert (tmp_path / "r.md").read_text(encoding="utf-8").startswith("# Findings summary")


def test_the_committed_export_and_report_are_up_to_date(clean_df, real):
    if not (config.FINDINGS_EXPORT.exists() and config.FINDINGS_REPORT.exists()):
        pytest.skip("run python run_pipeline.py first")
    committed = pd.read_csv(config.FINDINGS_EXPORT)
    assert list(committed["finding_id"]) == list(real["findings"]["finding_id"])
    assert config.FINDINGS_REPORT.read_text(encoding="utf-8") == rec.build_report(real["findings"], rec.analysis_windows(clean_df))
