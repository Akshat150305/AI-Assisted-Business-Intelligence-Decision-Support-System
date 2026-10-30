"""Tests for the report, the analytical summary and the slide content (src/reporting.py)."""
import pandas as pd
import pytest

from app_config import config
from src import anomaly_detection as ad
from src import anomaly_evaluation as ev
from src import metrics, recommendations, reporting
from src.filters import SalesFilter

CURRENT = SalesFilter(start_date="2025-01-01", end_date="2025-12-31")
PRIOR = SalesFilter(start_date="2024-01-01", end_date="2024-12-31")


@pytest.fixture(scope="module")
def pipeline(clean_df, datasets, cleaning_result):
    labels = datasets["anomaly_labels"].copy()
    labels["start_date"] = pd.to_datetime(labels["start_date"])
    labels["end_date"] = pd.to_datetime(labels["end_date"])
    labels = labels.fillna("")
    detections = ad.detect(clean_df)
    findings = recommendations.generate_findings(clean_df, detections)
    summary = ev.evaluate(detections, labels)["summary"]
    quality = reporting.quality_from_result(cleaning_result)
    facts = reporting.collect_facts(clean_df, detections, findings, quality=quality, evaluation=summary, dashboard_built=False)
    return {"facts": facts, "detections": detections, "findings": findings, "quality": quality, "evaluation": summary}


@pytest.fixture(scope="module")
def facts(pipeline):
    return pipeline["facts"]


def tiny(quarters):
    """A small clean table: one order per row, 100 revenue and 40% margin, over the given quarters."""
    rows = []
    for q in pd.period_range(quarters[0], quarters[1], freq="Q"):
        for i, d in enumerate(pd.date_range(q.start_time.normalize(), q.end_time.normalize(), periods=12).normalize()):
            for region in ("North", "South"):
                rows.append({"order_id": f"{q}-{region}-{i}", "line_number": 1, "order_date": d, "customer_id": f"C{i % 5}",
                             "customer_segment": "Consumer", "product_id": "P1", "product_name": "Alpha", "category": "Cat A",
                             "region": region, "quantity": 1, "unit_price": 100.0, "revenue": 100.0, "cost": 60.0, "profit": 40.0})
    return pd.DataFrame(rows)


def facts_for(df, **kw):
    detections = ad.detect(df)
    return reporting.collect_facts(df, detections, recommendations.generate_findings(df, detections), **kw)


# ----------------------------------------------------------------------------- numbers come from the calculations
def test_facts_match_the_metrics_module(clean_df, facts):
    assert facts["all"] == metrics.compute_kpis(clean_df)
    assert facts["current"] == metrics.compute_kpis(clean_df, CURRENT)
    assert facts["previous"] == metrics.compute_kpis(clean_df, PRIOR)
    assert facts["yoy"].loc["revenue", "pct_change"] == pytest.approx(metrics.pct_change(facts["current"]["revenue"], facts["previous"]["revenue"]))
    pd.testing.assert_frame_equal(facts["category"], metrics.breakdown(clean_df, "category", CURRENT))


def test_report_quotes_the_calculated_headline_numbers(clean_df, facts):
    text = reporting.build_report(facts)
    k, p = facts["current"], facts["previous"]
    for value in (reporting.money(k["revenue"]), reporting.money(k["profit"]), reporting.money(p["revenue"]),
                  f"{k['profit_margin_pct']:.2f}%", f"{k['distinct_orders']:,.0f}", f"{k['avg_order_value']:,.2f}"):
        assert value in text, value
    assert reporting.pct(metrics.pct_change(k["revenue"], p["revenue"])) in text


def test_the_margin_change_is_shown_in_percentage_points_not_as_a_percent_of_a_percent(facts):
    text = reporting.build_report(facts)
    change = facts["current"]["profit_margin_pct"] - facts["previous"]["profit_margin_pct"]
    assert reporting.pp(change) in text
    assert "| Profit margin |" in text and f"{reporting.pct(metrics.pct_change(facts['current']['profit_margin_pct'], facts['previous']['profit_margin_pct']))} |" not in text


def test_every_table_row_of_the_report_matches_the_breakdown(facts):
    text = reporting.build_report(facts)
    for table, key in (("category", "category"), ("region", "region"), ("segment", "customer_segment")):
        for _, r in facts[table].iterrows():
            assert f"| {r[key]} | {reporting.money(r['revenue'])} | {r['revenue_share_pct']:.1f}% |" in text


def test_the_top_findings_in_the_report_are_the_engines_top_findings(pipeline, facts):
    text = reporting.build_report(facts)
    for _, f in pipeline["findings"].head(5).iterrows():
        assert f"### {f['rank']}. {f['title']}" in text and f["observed_fact"] in text


def test_data_quality_numbers_come_from_the_cleaning_result(cleaning_result, facts):
    q = facts["quality"]
    assert q["raw_rows"] == cleaning_result.n_raw and q["clean_rows"] == len(cleaning_result.clean)
    assert q["rejected_rows"] == len(cleaning_result.rejected) and q["reconciles"] and q["problems_left"] == 0
    assert q["raw_rows"] == q["clean_rows"] + q["rejected_rows"]
    assert 0 < q["rejected_revenue_share_pct"] < 5
    assert f"{q['raw_rows']:,} rows" in reporting.build_report(facts)


def test_rejected_revenue_share_is_rejected_over_total(cleaning_result):
    class Fake:
        n_raw = 3
        clean = pd.DataFrame({"revenue": [90.0]})
        rejected = pd.DataFrame({"revenue": ["10", "not a number"]})
        audit = pd.DataFrame({"action": ["REPAIRED", "REMOVED", "REPAIRED"]})
        final_issues = pd.DataFrame()
        reconciles = False
    q = reporting.quality_from_result(Fake)
    assert q["rejected_revenue_share_pct"] == pytest.approx(10.0) and q["repairs"] == 2 and q["reconciles"] is False


def test_no_number_is_hard_coded_scaling_the_data_scales_the_text(clean_df):
    double = clean_df.copy()
    double[["revenue", "cost", "profit", "unit_price"]] *= 2
    base, scaled = facts_for(clean_df), facts_for(double)
    t1, t2 = reporting.build_report(base), reporting.build_report(scaled)
    assert reporting.money(base["current"]["revenue"]) in t1 and reporting.money(base["current"]["revenue"]) not in t2
    assert reporting.money(scaled["current"]["revenue"]) in t2
    s1, s2 = reporting.render_slides(reporting.build_slides(base), base), reporting.render_slides(reporting.build_slides(scaled), scaled)
    assert reporting.money(scaled["current"]["revenue"]) in s2 and reporting.money(scaled["current"]["revenue"]) not in s1


# ----------------------------------------------------------------------------- analytical summary
def test_summary_has_the_documented_columns_and_traceable_sources(facts):
    summary = reporting.build_summary(facts)
    assert list(summary.columns) == reporting.SUMMARY_COLUMNS
    assert summary["source"].str.len().gt(0).all() and summary["unit"].str.len().gt(0).all()
    assert {"headline", "current_window", "prior_window", "year_on_year_pct", "category", "region", "segment", "monthly",
            "anomaly", "anomaly_evaluation", "data_quality", "finding"} <= set(summary["section"])


def test_summary_values_equal_the_kpi_calculations(clean_df, facts):
    summary = reporting.build_summary(facts)
    kpis = metrics.compute_kpis(clean_df)
    for key in ("revenue", "profit", "profit_margin_pct", "distinct_orders", "avg_order_value", "active_customers"):
        value = summary[(summary["section"] == "headline") & (summary["metric"] == key)]["value"].iloc[0]
        assert value == pytest.approx(kpis[key])
    monthly = metrics.monthly_trend(clean_df).set_index("month")
    rows = summary[(summary["section"] == "monthly") & (summary["metric"] == "revenue")].set_index("period")["value"]
    assert rows.to_dict() == pytest.approx(monthly["revenue"].to_dict())


def test_summary_agrees_with_the_sql_exported_kpi_summary(db_path, facts):
    from src import database, queries
    connection = database.connect(db_path)
    try:
        sql = queries.kpi_summary(connection)
    finally:
        connection.close()
    summary = reporting.build_summary(facts)
    revenue = summary[(summary["section"] == "headline") & (summary["metric"] == "revenue")]["value"].iloc[0]
    assert revenue == pytest.approx(sql["revenue"])


def test_category_shares_in_the_summary_add_to_100(facts):
    summary = reporting.build_summary(facts)
    shares = summary[(summary["section"] == "category") & (summary["metric"] == "revenue_share_pct")]["value"]
    assert shares.sum() == pytest.approx(100.0)


def test_summary_finding_rows_match_the_ranked_findings(pipeline, facts):
    summary = reporting.build_summary(facts)
    rows = summary[(summary["section"] == "finding") & (summary["metric"] == "priority_score")]
    assert list(rows["scope"]) == list(pipeline["findings"].head(10)["finding_id"])


# ----------------------------------------------------------------------------- slides
def test_the_deck_covers_the_nine_required_topics(facts):
    slides = reporting.build_slides(facts)
    sections = " ".join(s["section"] for s in slides)
    for topic in ("1. Business problem", "2. Objectives and scope", "3. Solution architecture", "4. Data model and processing workflow",
                  "5. KPI framework", "6. Dashboard design and key findings", "7. Anomaly detection approach",
                  "8. Recommendations and next steps", "9. Limitations and future enhancements"):
        assert topic in sections, topic
    assert 10 <= len(slides) <= 14                                     # "short"
    assert all(s["bullets"] and s["notes"] and s["title"] for s in slides)


def test_dashboard_is_labelled_a_design_specification_until_it_is_built(facts):
    slides = reporting.build_slides(facts)
    dashboard = [s for s in slides if s["section"].startswith("6.") and "Design specification" in s["title"]]
    assert len(dashboard) == 1 and dashboard[0]["status"] == "design_specification"
    text = reporting.build_report(facts) + reporting.render_slides(slides, facts)
    assert "design specification" in text and "not yet a completed" in text


def test_a_built_dashboard_changes_the_label(pipeline):
    facts = dict(pipeline["facts"], dashboard_built=True)
    slides = reporting.build_slides(facts)
    assert any(s["status"] == "built" and "Built dashboard" in s["title"] for s in slides)
    assert "not yet a completed" not in reporting.build_report(facts) + reporting.render_slides(slides, facts)


def test_the_dashboard_flag_defaults_to_the_config_value(clean_df, pipeline):
    facts = reporting.collect_facts(clean_df, pipeline["detections"], pipeline["findings"])
    assert facts["dashboard_built"] is config.DASHBOARD_BUILT


def test_slide_numbers_are_the_calculated_numbers(facts):
    text = reporting.render_slides(reporting.build_slides(facts), facts)
    k = facts["current"]
    assert reporting.money(k["revenue"]) in text and f"{facts['quality']['raw_rows']:,}" in text
    assert f"{facts['anomaly']['flags']} flags in {facts['anomaly']['scored']:,}" in text
    assert f"{facts['evaluation']['events_detected']} of {facts['evaluation']['events']}" in text


# ----------------------------------------------------------------------------- honesty
def test_every_document_says_the_data_is_synthetic(facts):
    assert "synthetic" in reporting.build_report(facts).lower()
    assert "synthetic" in reporting.render_slides(reporting.build_slides(facts), facts).lower()
    assert any("synthetic" in s["bullets"][0].lower() or "synthetic" in s["notes"].lower() for s in reporting.build_slides(facts)[:1])


def test_no_unsupported_claims_in_the_generated_text(facts):
    assert reporting.check_claims(reporting.all_text(facts)) == []


@pytest.mark.parametrize("sentence", ["This saved hours of work", "It will increase revenue", "A guaranteed result", "ROI of 300%",
                                      "improved accuracy by 12%", "time saved per analyst", "increased revenue by 10%", "a proven approach"])
def test_the_claim_checker_catches_unsupported_claims(sentence):
    assert reporting.check_claims(sentence), sentence


def test_write_all_refuses_text_with_an_unsupported_claim(facts, tmp_path, monkeypatch):
    monkeypatch.setattr(reporting, "SYNTHETIC_NOTE", "This system will increase revenue.")
    with pytest.raises(ValueError, match="unsupported claims"):
        reporting.write_all(facts, tmp_path / "r.md", tmp_path / "s.csv", tmp_path / "p.md")
    assert not (tmp_path / "r.md").exists()


def test_anomaly_accuracy_is_not_reported_as_a_percentage(facts):
    text = reporting.build_report(facts) + reporting.render_slides(reporting.build_slides(facts), facts)
    assert "too few to estimate accuracy" in text
    for word in ("recall", "precision", "% accuracy", "accuracy of"):
        assert word not in text.lower()


def test_recommendations_are_actions_to_check_not_promised_outcomes(facts):
    text = reporting.build_report(facts)
    assert "None of them is a promise of a result" in text
    steps = reporting._next_steps(facts["findings"])
    assert steps and all(a in text for _, a, _ in steps)
    assert not any(t == "Profit margin is holding (-0.3 pp year on year)" for t, _, _ in steps)      # status findings are not actions


# ----------------------------------------------------------------------------- degenerate inputs
def test_empty_data_and_too_little_data_are_refused_with_a_clear_message():
    with pytest.raises(ValueError, match="empty"):
        reporting.collect_facts(tiny(("2025Q1", "2025Q1")).iloc[0:0], None, recommendations.empty_findings())
    short = tiny(("2025Q1", "2025Q1"))
    short = short[short["order_date"] < "2025-03-01"]
    with pytest.raises(ValueError, match="complete quarter"):
        reporting.collect_facts(short, ad.detect(short), recommendations.empty_findings())


def test_a_dataset_without_a_prior_year_still_produces_documents(tmp_path):
    df = tiny(("2025Q1", "2025Q3"))
    facts = facts_for(df)
    assert facts["yoy"] is None and facts["previous"] is None
    text = reporting.build_report(facts)
    assert "no earlier 12-month window" in text and "Change" not in text.split("## 3.")[0].split("## 2.")[1]
    written = reporting.write_all(facts, tmp_path / "r.md", tmp_path / "s.csv", tmp_path / "p.md")
    summary = pd.read_csv(written["summary"])
    assert "prior_window" not in set(summary["section"]) and "year_on_year_pct" not in set(summary["section"])
    assert len(reporting.build_slides(facts)) >= 10


def test_missing_quality_and_evaluation_are_handled(clean_df, pipeline):
    facts = reporting.collect_facts(clean_df, pipeline["detections"], pipeline["findings"], quality=None, evaluation=None, dashboard_built=False)
    text = reporting.build_report(facts)
    assert "were not supplied" in text and "planted in the synthetic data" not in text
    summary = reporting.build_summary(facts)
    assert not {"data_quality", "anomaly_evaluation"} & set(summary["section"])


def test_findings_that_are_empty_do_not_crash_the_report(clean_df, pipeline):
    flat = pd.DataFrame(columns=recommendations.FINDING_COLUMNS)
    facts = reporting.collect_facts(clean_df, pipeline["detections"], flat, dashboard_built=False)
    assert reporting.build_report(facts) and reporting.build_slides(facts)


# ----------------------------------------------------------------------------- writing and freshness
def test_write_all_writes_three_files_with_the_expected_content(facts, tmp_path):
    written = reporting.write_all(facts, tmp_path / "a" / "r.md", tmp_path / "a" / "s.csv", tmp_path / "b" / "p.md")
    assert written["report"].read_text(encoding="utf-8").startswith("# Business Findings Report")
    assert written["slides"].read_text(encoding="utf-8").startswith("# Stakeholder presentation")
    summary = pd.read_csv(written["summary"])
    assert list(summary.columns) == reporting.SUMMARY_COLUMNS and len(summary) == written["summary_rows"]


def test_generation_is_deterministic(facts):
    assert reporting.all_text(facts) == reporting.all_text(facts)
    pd.testing.assert_frame_equal(reporting.build_summary(facts), reporting.build_summary(facts))


@pytest.mark.parametrize("path_name, builder", [
    ("BUSINESS_REPORT", lambda f: reporting.build_report(f)),
    ("SLIDE_CONTENT", lambda f: reporting.render_slides(reporting.build_slides(f), f)),
])
def test_committed_documents_are_up_to_date(facts, path_name, builder):
    path = getattr(config, path_name)
    if not path.exists():
        pytest.skip("run python run_pipeline.py first")
    committed = path.read_text(encoding="utf-8")
    current = builder(dict(facts, dashboard_built=config.DASHBOARD_BUILT))
    assert committed == current, f"{path.name} is stale: run python run_pipeline.py"


def test_the_committed_summary_is_up_to_date(facts):
    if not config.ANALYTICAL_SUMMARY.exists():
        pytest.skip("run python run_pipeline.py first")
    committed = pd.read_csv(config.ANALYTICAL_SUMMARY)
    current = reporting.build_summary(dict(facts, dashboard_built=config.DASHBOARD_BUILT)).round({"value": 4})
    pd.testing.assert_frame_equal(committed, current, check_dtype=False)


# ----------------------------------------------------------------------------- direct checks of three things that were only covered by the freshness test
def test_the_slide_file_opens_with_the_synthetic_data_banner(facts):
    text = reporting.render_slides(reporting.build_slides(facts), facts)
    assert "> **Synthetic data.**" in text.split("## Slide 1")[0]


def test_the_anomaly_slide_itself_carries_the_accuracy_disclaimer(facts):
    slide = next(s for s in reporting.build_slides(facts) if s["section"].startswith("7."))
    assert any("too few to estimate accuracy" in b for b in slide["bullets"])
    assert any("not a confirmed issue" in b for b in slide["bullets"])


def test_the_summary_has_one_row_per_breakdown_value_and_metric(facts):
    summary = reporting.build_summary(facts)
    for section, table in (("category", "category"), ("region", "region"), ("segment", "segment")):
        rows = summary[summary["section"] == section]
        assert set(rows["metric"]) == {"revenue", "profit", "profit_margin_pct", "revenue_share_pct", "avg_order_value"}
        assert len(rows) == 5 * len(facts[table])


def test_counts_on_the_slides_come_from_the_specification_and_the_catalogue(facts):
    from src import kpi_catalogue, powerbi_spec
    slides = reporting.build_slides(facts)
    kpi_slide = next(s for s in slides if s["section"].startswith("5."))
    dashboard = next(s for s in slides if s["status"] == "design_specification")
    assert f"{len(kpi_catalogue.KPIS)} documented KPIs" in kpi_slide["title"]
    assert f"{len(powerbi_spec.MEASURES)} DAX measures" in " ".join(dashboard["bullets"])
    assert powerbi_spec.PAGE_NAMES[0] in dashboard["bullets"][0] and powerbi_spec.PAGE_NAMES[-1] in dashboard["bullets"][0]
