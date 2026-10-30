"""Reporting: the business findings report, an exportable analytical summary and the stakeholder slide content.

Everything is written FROM the pipeline's calculated results; no KPI value is typed into this file.
One function collects the facts (``collect_facts``); three writers turn the same facts into

  reports/business_findings_report.md   concise report for a business reader
  reports/analytical_summary.csv        the same numbers in long format, with their source (FR-33)
  presentation/slide_content.md         the slides of the stakeholder deck (text, numbers and speaker notes)

Honesty rules (brief, Phase 7), enforced by tests/test_reporting.py
------------------------------------------------------------------
* ALL DATA IS SYNTHETIC and every document says so.
* The Power BI dashboard is called a DESIGN SPECIFICATION until config.DASHBOARD_BUILT is set to True
  (only after it has really been built and checked).
* No claim of revenue improvement, accuracy improvement or time saved: none has been measured.
  Words such as "saved", "ROI" or "improved accuracy" are rejected by a test.
* Recommendations are things to check, taken from the findings; they promise no result.
* The four planted anomalies are too few to estimate detection accuracy, and the documents say that.
"""
import re

import numpy as np
import pandas as pd

from app_config import config
from src import anomaly_detection, kpi_catalogue, metrics, powerbi_spec, recommendations
from src.filters import SalesFilter

SYNTHETIC_NOTE = ("All data in this project is synthetic: generated from a fixed random seed for a portfolio project. "
                  "It describes a simulated retail business, not a real company.")
DASHBOARD_SPEC_NOTE = ("The Power BI dashboard is a design specification (powerbi/dashboard_spec.md, dax_measures.md, "
                       "build_guide.md). It is not yet a completed, validated deliverable.")
DASHBOARD_BUILT_NOTE = ("The Power BI dashboard has been built from powerbi/dashboard_spec.md and its KPI cards were checked "
                        "against the pipeline's answer key (powerbi/build_guide.md section 8).")
FORBIDDEN_CLAIMS = [r"\btime saved\b", r"\bsav(?:e|es|ed|ing|ings)\s+(?:\w+\s+){0,2}(?:hours|time|days|money|costs?)\b", r"\bROI\b", r"\bimproved? accuracy\b",
                    r"\baccuracy (?:improv|of \d)", r"\bincreased? revenue by\b", r"\bwill (?:increase|boost|improve|grow)\b",
                    r"\bguarantee", r"\bproven\b"]


def dashboard_note(built=None):
    return DASHBOARD_BUILT_NOTE if (config.DASHBOARD_BUILT if built is None else built) else DASHBOARD_SPEC_NOTE


# ============================================================================= facts
def quality_from_result(result):
    """Data-quality numbers from a data_pipeline.CleaningResult."""
    rejected_revenue = pd.to_numeric(result.rejected["revenue"], errors="coerce").sum() if len(result.rejected) else 0.0
    clean_revenue = float(result.clean["revenue"].sum())
    return {
        "raw_rows": int(result.n_raw), "clean_rows": int(len(result.clean)), "rejected_rows": int(len(result.rejected)),
        "repairs": int((result.audit["action"] == "REPAIRED").sum()) if len(result.audit) else 0,
        "rejected_revenue_share_pct": float(rejected_revenue) * 100.0 / (clean_revenue + float(rejected_revenue)) if clean_revenue else None,
        "reconciles": bool(result.reconciles), "problems_left": int(len(result.final_issues)),
    }


def quality_from_files(raw_path=config.RAW_SALES_FILE, clean_rows=None):
    """Same numbers from the files the pipeline wrote (used by run_pipeline.py)."""
    from src import data_pipeline
    return quality_from_result(data_pipeline.clean_sales(data_pipeline.load_raw_sales(raw_path)))


def _by(df, by, window):
    return metrics.breakdown(df, by, SalesFilter(start_date=window[0], end_date=window[1]))


def collect_facts(clean, detections, findings, quality=None, evaluation=None, dashboard_built=None):
    """Gather every number the documents use. ``evaluation`` is anomaly_evaluation's ``summary`` dict (optional)."""
    if clean is None or clean.empty:
        raise ValueError("cannot write a report from an empty sales table")
    windows = recommendations.analysis_windows(clean)
    if not windows["current"]:
        raise ValueError("less than one complete quarter of data: no report can be written")
    window, prior = windows["current"], windows["prior"]
    current = metrics.compute_kpis(clean, SalesFilter(start_date=window[0], end_date=window[1]))
    previous = metrics.compute_kpis(clean, SalesFilter(start_date=prior[0], end_date=prior[1])) if prior else None
    yoy = metrics.comparison_table(current, previous).set_index("kpi") if previous else None
    monthly = metrics.monthly_trend(clean)
    flagged = detections[detections["is_anomaly"]] if detections is not None and len(detections) else pd.DataFrame()
    scored = int((detections["status"] == "evaluated").sum()) if detections is not None and len(detections) else 0
    return {
        "date_range": (clean["order_date"].min().strftime("%Y-%m-%d"), clean["order_date"].max().strftime("%Y-%m-%d")),
        "windows": windows, "window": window, "prior_window": prior,
        "all": metrics.compute_kpis(clean), "current": current, "previous": previous, "yoy": yoy,
        "category": _by(clean, "category", window), "region": _by(clean, "region", window),
        "segment": _by(clean, "customer_segment", window),
        "monthly": monthly, "peak_month": monthly.loc[monthly["revenue"].idxmax()], "low_month": monthly.loc[monthly["revenue"].idxmin()],
        "n_products": int(clean["product_id"].nunique()), "n_customers": int(clean["customer_id"].nunique()),
        "n_categories": int(clean["category"].nunique()), "n_regions": int(clean["region"].nunique()),
        "findings": findings, "finding_counts": findings["finding_type"].value_counts().to_dict() if len(findings) else {},
        "anomaly": {"flags": int(len(flagged)), "scored": scored, "rate_pct": (len(flagged) * 100.0 / scored) if scored else None,
                    "by_severity": flagged["severity"].value_counts().to_dict() if len(flagged) else {},
                    "series": int(detections[["scope_type", "scope_value", "metric"]].drop_duplicates().shape[0]) if scored else 0,
                    "weeks": int(detections["week_start"].nunique()) if scored else 0,
                    "recent_findings": int((findings["rule"] == "ANOMALY").sum()) if len(findings) else 0},
        "evaluation": evaluation, "quality": quality,
        "dashboard_built": config.DASHBOARD_BUILT if dashboard_built is None else dashboard_built,
    }


# ============================================================================= helpers
money, pct, pp = recommendations.money, recommendations.pct, recommendations.pp


def _table(headers, rows, align=None):
    align = align or ["---"] * len(headers)
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(align) + "|"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def _breakdown_rows(table, key):
    return [(r[key], money(r["revenue"]), f"{r['revenue_share_pct']:.1f}%", money(r["profit"]),
             f"{r['profit_margin_pct']:.1f}%") for _, r in table.iterrows()]


def _rate(a):
    """Flag rate as text; 'n/a' when no series-week could be scored (too little history, or flat data)."""
    return "n/a" if a["rate_pct"] is None else f"{a['rate_pct']:.2f}%"


def _label(window):
    return f"{window[0]} to {window[1]}"


def _change_text(facts, kpi):
    """Year-on-year change as text. A margin is already a percentage, so its change is shown in percentage points."""
    yoy = facts["yoy"]
    if yoy is None:
        return "n/a"
    if kpi == "profit_margin_pct":
        return "n/a" if pd.isna(yoy.loc[kpi, "absolute_change"]) else pp(yoy.loc[kpi, "absolute_change"])
    return "n/a" if pd.isna(yoy.loc[kpi, "pct_change"]) else pct(yoy.loc[kpi, "pct_change"])


def _next_steps(findings, limit=4):
    """Distinct recommended actions of the top findings (they are checks, written by the findings engine)."""
    steps, seen = [], set()
    for _, f in findings.iterrows():
        if f["finding_type"] == "status" or f["recommended_action"] in seen:
            continue
        seen.add(f["recommended_action"])
        steps.append((f["title"], f["recommended_action"], f["evidence_needed"]))
        if len(steps) == limit:
            break
    return steps


# ============================================================================= the business report
def build_report(facts):
    k, y, w = facts["current"], facts["yoy"], facts["window"]
    a, q, ev = facts["anomaly"], facts["quality"], facts["evaluation"]
    lines = ["# Business Findings Report", "",
             "_Generated by `python run_pipeline.py` (src/reporting.py). Do not edit by hand._", "",
             f"> **Synthetic data.** {SYNTHETIC_NOTE}", ">", f"> {dashboard_note(facts['dashboard_built'])}", "",
             "## 1. Summary", ""]
    if y is not None:
        lines += [f"- Over {_label(w)}, revenue was **{money(k['revenue'])}** ({_change_text(facts, 'revenue')} vs {_label(facts['prior_window'])}) "
                  f"and profit **{money(k['profit'])}** ({_change_text(facts, 'profit')}). The profit margin was **{k['profit_margin_pct']:.1f}%** "
                  f"({_change_text(facts, 'profit_margin_pct')} year on year).",
                  f"- Looking at what moved, the change is {recommendations._volume_or_value(k, facts['previous']) or 'spread across order activity'}: "
                  f"distinct orders {_change_text(facts, 'distinct_orders')}, average order value {_change_text(facts, 'avg_order_value')}, "
                  f"active customers {_change_text(facts, 'active_customers')}."]
    else:
        lines += [f"- Over {_label(w)}, revenue was **{money(k['revenue'])}** and profit **{money(k['profit'])}** "
                  f"(margin {k['profit_margin_pct']:.1f}%). There is no earlier 12-month window to compare with."]
    top = facts["findings"].head(3)
    for _, f in top.iterrows():
        lines.append(f"- **{f['title']}** (confidence {f['confidence']}).")
    lines += [f"- {a['flags']} of {a['scored']:,} scored series-weeks were flagged as statistical anomalies "
              f"({_rate(a)}). They are leads to investigate, not confirmed problems.", ""]

    lines += ["## 2. Headline numbers", "",
              _table(["Measure", f"{_label(w)}"] + ([f"{_label(facts['prior_window'])}", "Change"] if y is not None else []),
                     [(name, fmt(k[key])) + ((fmt(facts["previous"][key]), _change_text(facts, key)) if y is not None else ())
                      for name, key, fmt in (("Revenue", "revenue", money), ("Profit", "profit", money),
                                             ("Profit margin", "profit_margin_pct", lambda v: f"{v:.2f}%"),
                                             ("Distinct orders", "distinct_orders", lambda v: f"{v:,.0f}"),
                                             ("Average order value", "avg_order_value", lambda v: f"{v:,.2f}"),
                                             ("Active customers", "active_customers", lambda v: f"{v:,.0f}"))],
                     ["---", "---:"] + (["---:", "---:"] if y is not None else [])), "",
              f"All data ({facts['date_range'][0]} to {facts['date_range'][1]}): revenue {money(facts['all']['revenue'])}, "
              f"profit {money(facts['all']['profit'])}, {facts['all']['distinct_orders']:,} distinct orders, "
              f"{facts['all']['active_customers']:,} customers. Highest month: {facts['peak_month']['month']} "
              f"({money(facts['peak_month']['revenue'])}); lowest: {facts['low_month']['month']} ({money(facts['low_month']['revenue'])}).", ""]

    heads = ["Revenue", "Share", "Profit", "Margin"]
    lines += [f"## 3. Where revenue and profit come from ({_label(w)})", "",
              "### By category", "", _table(["Category"] + heads, _breakdown_rows(facts["category"], "category"), ["---"] + ["---:"] * 4), "",
              "### By region", "", _table(["Region"] + heads, _breakdown_rows(facts["region"], "region"), ["---"] + ["---:"] * 4), "",
              "### By customer segment", "", _table(["Segment"] + heads, _breakdown_rows(facts["segment"], "customer_segment"), ["---"] + ["---:"] * 4), "",
              "Distinct orders are not added across categories (one order can contain several categories), so they are not totalled here.", ""]

    lines += ["## 4. Key findings", "",
              f"The rule-based engine produced {len(facts['findings'])} findings ({', '.join(f'{n} {t}' for t, n in facts['finding_counts'].items())}). "
              "The ten highest-priority are in `reports/findings_summary.md`; the top five are below. Each separates the observed fact "
              "from a possible explanation (a hypothesis) and an action to check.", ""]
    for _, f in facts["findings"].head(5).iterrows():
        lines += [f"### {f['rank']}. {f['title']}", "",
                  f"*Confidence: {f['confidence']}. Profit at stake: {money(f['profit_impact'])} ({f['priority_score']:.1f}% of the window's profit).*", "",
                  f"- **Fact:** {f['observed_fact']}", f"- **Possible explanation:** {f['possible_explanation']}",
                  f"- **Action to consider:** {f['recommended_action']}", f"- **Evidence needed:** {f['evidence_needed']}", ""]

    lines += ["## 5. Anomalies", "",
              f"Weekly revenue, profit and margin were scored for {a['series']} series over {a['weeks']} complete weeks: "
              f"{a['flags']} flags in {a['scored']:,} scored series-weeks ({_rate(a)})"
              + ("; by severity " + ", ".join(f"{n} {sev}" for sev, n in a["by_severity"].items()) if a["by_severity"] else "") + ". "
              f"{a['recent_findings']} anomaly findings were created from recent high or extreme flags. "
              f"The baseline is the median of the previous {config.ANOMALY_WINDOW_WEEKS} weeks and a week is flagged at {config.ANOMALY_THRESHOLD:g} or more "
              "robust deviations. A flag means 'unusual compared with recent weeks'; it does not say why and does not predict anything. "
              "Seasonal peaks can be flagged because a rolling baseline cannot anticipate them.", ""]
    if ev:
        lines += [f"Against the four anomalies planted in the synthetic data, {ev['events_detected']} of {ev['events']} were detected "
                  f"and {ev['flags_unexplained']} of the flags matched no planted event. Four events are too few to estimate accuracy; "
                  "see `reports/anomaly_evaluation.md`, including how the parameters were revised.", ""]

    lines += ["## 6. Data quality", ""]
    if q:
        share = f"{q['rejected_revenue_share_pct']:.2f}%" if q["rejected_revenue_share_pct"] is not None else "n/a"
        lines += [f"The raw file had {q['raw_rows']:,} rows. {q['rejected_rows']:,} were quarantined and {q['repairs']:,} individual values were repaired "
                  f"(each logged); {q['clean_rows']:,} rows were analysed. Raw rows = clean rows + quarantined rows: "
                  f"{'yes' if q['reconciles'] else 'NO'}. Validation problems left in the clean table: {q['problems_left']}. "
                  f"The quarantined rows carried about {share} of the revenue, which is therefore missing from every number above. "
                  "Details: `reports/data_quality_summary.md`.", ""]
    else:
        lines += ["Data-quality numbers were not supplied for this run; see `reports/data_quality_summary.md`.", ""]

    lines += ["## 7. Recommended next steps", "",
              "These are checks and decisions suggested by the findings. None of them is a promise of a result.", ""]
    for n, (title, action, needed) in enumerate(_next_steps(facts["findings"]), 1):
        lines += [f"{n}. **{title}.** {action} *Evidence needed: {needed}*"]
    lines += ["", "## 8. Limitations", "",
              "- Synthetic data: the patterns were designed into it. The findings show what the method reports on this data, not what a real business would see.",
              "- Thresholds for findings and anomalies are judgement calls fixed in `app_config/config.py`; other thresholds give other findings.",
              "- Findings are descriptive. They do not forecast and they do not establish causes.",
              "- Two years of data allow one year-on-year comparison, which is not a trend.",
              "- No business impact (on revenue, accuracy or efficiency) has been measured, and none is claimed.", ""]
    return "\n".join(lines)


# ============================================================================= the exportable summary
SUMMARY_COLUMNS = ["section", "scope", "metric", "period", "value", "unit", "source"]


def build_summary(facts):
    """Long-format table: every number used in the documents with the code that produced it."""
    rows = []

    def add(section, scope, metric, period, value, unit, source):
        rows.append({"section": section, "scope": scope, "metric": metric, "period": period,
                     "value": None if value is None or (isinstance(value, float) and np.isnan(value)) else float(value),
                     "unit": unit, "source": source})

    units = {"revenue": "currency", "profit": "currency", "profit_margin_pct": "percent", "distinct_orders": "count",
             "avg_order_value": "currency", "active_customers": "count", "units_sold": "count"}
    rng = f"{facts['date_range'][0]} to {facts['date_range'][1]}"
    for key, unit in units.items():
        add("headline", "All", key, rng, facts["all"][key], unit, "metrics.compute_kpis")
        add("current_window", "All", key, _label(facts["window"]), facts["current"][key], unit, "metrics.compute_kpis")
        if facts["previous"]:
            add("prior_window", "All", key, _label(facts["prior_window"]), facts["previous"][key], unit, "metrics.compute_kpis")
            add("year_on_year_pct", "All", key, _label(facts["window"]), facts["yoy"].loc[key, "pct_change"], "percent", "metrics.compare_periods")
    for section, key, label in (("category", "category", "category"), ("region", "region", "region"), ("segment", "customer_segment", "segment")):
        for _, r in facts[section].iterrows():
            for metric, unit in (("revenue", "currency"), ("profit", "currency"), ("profit_margin_pct", "percent"),
                                 ("revenue_share_pct", "percent"), ("avg_order_value", "currency")):
                add(section, r[key], metric, _label(facts["window"]), r[metric], unit, "metrics.breakdown")
    for _, r in facts["monthly"].iterrows():
        add("monthly", "All", "revenue", r["month"], r["revenue"], "currency", "metrics.monthly_trend")
        add("monthly", "All", "profit", r["month"], r["profit"], "currency", "metrics.monthly_trend")
    a = facts["anomaly"]
    add("anomaly", "All", "flags", rng, a["flags"], "count", "anomaly_detection.detect")
    add("anomaly", "All", "scored_series_weeks", rng, a["scored"], "count", "anomaly_detection.detect")
    add("anomaly", "All", "flag_rate_pct", rng, a["rate_pct"], "percent", "anomaly_detection.detect")
    for severity, n in a["by_severity"].items():
        add("anomaly", severity, "flags", rng, n, "count", "anomaly_detection.detect")
    if facts["evaluation"]:
        add("anomaly_evaluation", "planted events", "events", rng, facts["evaluation"]["events"], "count", "anomaly_evaluation.evaluate")
        add("anomaly_evaluation", "planted events", "events_detected", rng, facts["evaluation"]["events_detected"], "count", "anomaly_evaluation.evaluate")
    if facts["quality"]:
        for key, unit in (("raw_rows", "count"), ("clean_rows", "count"), ("rejected_rows", "count"), ("repairs", "count"),
                          ("rejected_revenue_share_pct", "percent")):
            add("data_quality", "All", key, "all data", facts["quality"][key], unit, "data_pipeline.clean_sales")
    for _, f in facts["findings"].head(10).iterrows():
        add("finding", f["finding_id"], "priority_score", f["period"], f["priority_score"], "percent of window profit", "recommendations.generate_findings")
        add("finding", f["finding_id"], "profit_impact", f["period"], f["profit_impact"], "currency", "recommendations.generate_findings")
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


# ============================================================================= the stakeholder slides
def build_slides(facts):
    """The slides as data: dict(section, title, bullets, notes, status). Status marks design-only content."""
    k, y, w, a, q, ev = facts["current"], facts["yoy"], facts["window"], facts["anomaly"], facts["quality"], facts["evaluation"]
    built = facts["dashboard_built"]
    top = facts["findings"].head(5)
    cat_weak = facts["category"].sort_values("profit_margin_pct").iloc[0]
    cat_strong = facts["category"].sort_values("profit_margin_pct").iloc[-1]
    slides = []

    def slide(section, title, bullets, notes, status="measured"):
        slides.append({"section": section, "title": title, "bullets": bullets, "notes": notes, "status": status})

    slide("Title", "AI-Assisted Business Intelligence & Decision Support System",
          ["A retail BI portfolio project on synthetic data", f"Data period: {facts['date_range'][0]} to {facts['date_range'][1]}",
           "Python, SQLite, Power BI (specification), pytest"],
          SYNTHETIC_NOTE + " Say this first.", "context")
    slide("1. Business problem", "Business teams need trusted numbers and a short list of what to look at",
          ["Sales data sits in raw order lines with errors, duplicates and inconsistent labels",
           "Managers ask: is revenue growing, is margin holding, which products and regions need attention?",
           "KPIs are often defined differently in each report, so numbers disagree",
           "Unusual weeks are noticed late, or only by chance"],
          "Frame the problem as decision support: trusted numbers, one KPI definition, a ranked list of leads. No benefit to efficiency or accuracy is claimed; none was measured.", "context")
    slide("2. Objectives and scope", "Scope: from raw order lines to ranked, evidence-backed findings",
          ["Generate a reproducible synthetic dataset with known planted defects and anomalies",
           "Validate and clean it with an audit trail", "Define KPIs once and calculate them in SQL and Python, with tests that they agree",
           "Detect unusual weeks and turn measured changes into findings", "Specify a 5-page Power BI dashboard",
           "Out of scope: forecasting, causal claims, real company data"],
          "Scope boundaries matter in interviews: the project is descriptive analytics, not prediction.", "context")
    slide("3. Solution architecture", "Python is the source of truth; SQL and Power BI present the same numbers",
          ["Generator -> raw CSV -> validation and cleaning -> clean table (+ quarantine and audit log)",
           "Clean table -> SQLite star-style database -> parameterised SQL KPI queries",
           "Python metrics, anomaly detection and findings engine read the same clean table",
           "Exports: star schema (fact + 3 dimensions), anomaly table, findings table, KPI answer keys",
           "One command, python run_pipeline.py, rebuilds everything"],
          "Mention that SQL and Python results are tested to agree under filters, including empty selections.", "measured")
    quality_bullets = ([f"{q['raw_rows']:,} raw rows; {q['rejected_rows']:,} quarantined; {q['repairs']:,} values repaired and logged; "
                        f"{q['clean_rows']:,} analysed", "Raw = clean + quarantined: " + ("verified" if q["reconciles"] else "NOT reconciled"),
                        "Repair only when a value can be derived; otherwise quarantine"] if q else ["Data-quality figures: see reports/data_quality_summary.md"])
    slide("4. Data model and processing workflow", "A star schema over one fact table; every cleaning step is logged",
          ["Fact: one row per order line, key (order_id, line_number); distinct orders counted with DISTINCTCOUNT", *quality_bullets,
           "Dimensions: product (category), customer (region, segment), date"],
          "Grain is the most common interview question: one row = one order line, so orders must be counted distinctly and are not additive across categories.", "measured")
    slide("5. KPI framework", f"{len(kpi_catalogue.KPIS)} documented KPIs, one definition each",
          ["Revenue, profit, profit margin %, distinct orders, average order value, active customers, repeat rate and more",
           f"Profit margin = total profit / total revenue (a ratio of totals): {k['profit_margin_pct']:.1f}% in {_label(w)}",
           f"Average order value = revenue / distinct orders: {k['avg_order_value']:,.2f}",
           "Change % = (current - previous) / ABS(previous); zero denominators return blank, never an error",
           "docs/kpi_catalogue.md lists meaning, formula, source and caveats for each"],
          "Ratios are computed from totals, never by averaging row-level ratios.", "measured")
    spec_label = "Design specification" if not built else "Built dashboard"
    slide("6. Dashboard design and key findings", f"{spec_label}: {len(powerbi_spec.PAGES)} pages for {len({p['stakeholder'] for p in powerbi_spec.PAGES})} stakeholders",
          [", ".join(powerbi_spec.PAGE_NAMES),
           f"Date, region, category and segment slicers; {len(powerbi_spec.MEASURES)} DAX measures specified",
           "KPI cards are to be checked against the SQL-computed answer key"
           if not built else "KPI cards were checked against the SQL-computed answer key",
           dashboard_note(built)],
          "Be explicit: this slide shows the design. Do not describe the dashboard as finished until it is built and validated.",
          "design_specification" if not built else "built")
    slide("6. Dashboard design and key findings", f"What the data shows, {_label(w)}",
          [f"Revenue {money(k['revenue'])} ({_change_text(facts, 'revenue')}), profit {money(k['profit'])} ({_change_text(facts, 'profit')}), "
           f"margin {k['profit_margin_pct']:.1f}%",
           f"Highest margin category: {cat_strong['category']} ({cat_strong['profit_margin_pct']:.1f}%); lowest: {cat_weak['category']} ({cat_weak['profit_margin_pct']:.1f}%)",
           f"Largest region: {facts['region'].iloc[0]['region']} ({facts['region'].iloc[0]['revenue_share_pct']:.0f}% of revenue)",
           f"Largest segment: {facts['segment'].iloc[0]['customer_segment']} ({facts['segment'].iloc[0]['revenue_share_pct']:.0f}% of revenue)",
           *[f"Finding {f['rank']}: {f['title']} ({f['confidence']} confidence)" for _, f in top.head(3).iterrows()]],
          "Every number comes from the pipeline. Findings carry confidence levels and limitations; the explanations are hypotheses.", "measured")
    anomaly_bullets = [f"Weekly revenue, profit and margin for {a['series']} series; baseline = median of the previous {config.ANOMALY_WINDOW_WEEKS} weeks (no future data)",
                       f"Flag at {config.ANOMALY_THRESHOLD:g} or more robust deviations (median/MAD)",
                       f"Result: {a['flags']} flags in {a['scored']:,} scored series-weeks ({_rate(a)})",
                       "A flag is a statistical anomaly to investigate, not a confirmed issue or a forecast"]
    if ev:
        anomaly_bullets.append(f"Planted test events detected: {ev['events_detected']} of {ev['events']} (too few to estimate accuracy)")
    slide("7. Anomaly detection approach", "Explainable, prior-data-only anomaly detection", anomaly_bullets,
          "Disclose the parameter revision: the first settings (8 weeks, 3.5) flagged about 1 week in 20 on pure noise, so they were changed using only a noise simulation, not the planted labels.", "measured")
    steps = _next_steps(facts["findings"], 3)
    slide("8. Recommendations and next steps", "Checks suggested by the findings (not promised outcomes)",
          [f"{title}: {action}" for title, action, _ in steps] + ["Each recommendation lists the evidence needed to confirm or reject its explanation"],
          "Recommendations come from the rule-based engine; they are things to check or decide. Real-data validation would be the next step.", "measured")
    slide("9. Limitations and future enhancements", "What this project does not show",
          ["Synthetic data: patterns were designed in; results do not describe a real business",
           "No measured business impact: nothing about revenue, accuracy or efficiency is claimed",
           "Thresholds are judgement calls; findings are descriptive, not causal or predictive",
           "Four planted anomalies cannot estimate detection accuracy",
           "Future: real data source, seasonality-aware baseline, forecasting, validated user feedback, optional LLM wording of findings"],
          "Finish on honesty; it is a strength in interviews.", "context")
    return slides


def render_slides(slides, facts):
    lines = ["# Stakeholder presentation: slide content", "",
             "_Generated by `python run_pipeline.py` (src/reporting.py). Do not edit by hand._", "",
             f"> **Synthetic data.** {SYNTHETIC_NOTE}", ">", f"> {dashboard_note(facts['dashboard_built'])}", "",
             "Slide status: `measured` = numbers from the pipeline, `design_specification` = not a completed deliverable, "
             "`context` = framing text.", ""]
    for n, s in enumerate(slides, 1):
        lines += [f"## Slide {n}: {s['title']}", "", f"*Section: {s['section']} | status: {s['status']}*", "",
                  *[f"- {b}" for b in s["bullets"]], "", f"**Speaker notes:** {s['notes']}", ""]
    return "\n".join(lines)


# ============================================================================= writing
def all_text(facts):
    return build_report(facts) + "\n" + render_slides(build_slides(facts), facts)


def check_claims(text):
    """Return the unsupported-claim patterns found in ``text`` (should be empty)."""
    return [p for p in FORBIDDEN_CLAIMS if re.search(p, text, flags=re.IGNORECASE)]


def write_all(facts, report_path=config.BUSINESS_REPORT, summary_path=config.ANALYTICAL_SUMMARY, slides_path=config.SLIDE_CONTENT):
    problems = check_claims(all_text(facts))
    if problems:
        raise ValueError(f"unsupported claims in generated text: {problems}")
    for path in (report_path, summary_path, slides_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(build_report(facts), encoding="utf-8")
    slides_path.write_text(render_slides(build_slides(facts), facts), encoding="utf-8")
    summary = build_summary(facts)
    summary.round({"value": 4}).to_csv(summary_path, index=False)
    return {"report": report_path, "summary": summary_path, "slides": slides_path, "summary_rows": len(summary)}
