"""Score the anomaly detector against the planted (synthetic) anomaly labels.

THIS is the only module that reads the planted labels, and it only runs AFTER detection has
finished. src/anomaly_detection.py never imports this module and cannot see the labels, which is
what "no label leakage" means in practice (tests/test_anomaly_detection.py checks it).

What "detected" means here
--------------------------
Each planted event has a PRIMARY series (the slice and metric it was designed to move) and a
direction. The event counts as detected when the primary series is flagged, in the right direction,
in a complete week that overlaps the event's dates.

A flag inside an event's window on any series that overlaps the affected slice is "explained" by
the event (a South volume spike also moves total revenue and every category's South sales). Flags
that match no planted event are "unexplained". With synthetic data the unexplained flags are the
detector's false alarms with respect to the planted events, but they may also be genuine features
the generator built in (seasonality, regional growth), which a simple baseline cannot anticipate.

Small-sample warning: there are only 4 planted events, so recall figures are anecdotes, not a
reliable accuracy estimate.

ALL DATA IS SYNTHETIC.
"""
import numpy as np
import pandas as pd

from app_config import config
from src import anomaly_detection as ad

LABEL_METRIC = {"revenue": "revenue", "profit": "profit", "profit_margin": "profit_margin_pct"}
EXPECTED_DIRECTION = {
    "revenue_spike": "high", "revenue_drop": "low",
    "margin_squeeze": "low", "single_large_order": "high",
}


# ----------------------------------------------------------------------------- labels
def load_labels(path=config.ANOMALY_LABELS_FILE):
    """Read the planted-anomaly label file. Blank cells become empty strings."""
    labels = pd.read_csv(path, dtype=str, keep_default_na=False)
    labels["start_date"] = pd.to_datetime(labels["start_date"])
    labels["end_date"] = pd.to_datetime(labels["end_date"])
    return labels


def label_scope(label):
    """The (scope_type, scope_value) series a planted event was designed to move."""
    if label["category"]:
        return "category", label["category"]
    if label["region"]:
        return "region", label["region"]
    return "all", "All"


def overlaps_window(week_start, week_end, label):
    return (week_start <= label["end_date"]) & (week_end >= label["start_date"])


def scopes_overlap(event_scope, series_scope):
    """True unless both are the same kind of slice with different values (South vs West)."""
    if event_scope == ("all", "All"):
        return True
    if event_scope[0] == series_scope[0]:
        return event_scope[1] == series_scope[1]
    return True


# ----------------------------------------------------------------------------- scoring
def evaluate(detections, labels):
    """Compare detections with planted labels.

    Returns a dict with:
      per_event : one row per planted event (detected or not, how strongly, how early)
      flags     : every flag with an ``explained_by`` column (event id or None)
      summary   : counts
    """
    flags = ad.flags_only(detections)
    flags["explained_by"] = None
    rows = []
    for _, label in labels.iterrows():
        scope = label_scope(label)
        metric = LABEL_METRIC[label["affected_metric"]]
        direction = EXPECTED_DIRECTION[label["anomaly_type"]]
        in_window = overlaps_window(flags["week_start"], flags["week_end"], label)
        related = in_window & flags.apply(lambda r: scopes_overlap(scope, (r["scope_type"], r["scope_value"])), axis=1)
        flags.loc[related & flags["explained_by"].isna(), "explained_by"] = label["anomaly_id"]
        primary = flags[in_window & (flags["scope_type"] == scope[0]) & (flags["scope_value"] == scope[1])
                        & (flags["metric"] == metric) & (flags["direction"] == direction)]
        weeks = detections[(detections["scope_type"] == scope[0]) & (detections["scope_value"] == scope[1])
                           & (detections["metric"] == metric)
                           & overlaps_window(detections["week_start"], detections["week_end"], label)]
        rows.append({
            "anomaly_id": label["anomaly_id"],
            "anomaly_type": label["anomaly_type"],
            "window": f"{label['start_date']:%Y-%m-%d} to {label['end_date']:%Y-%m-%d}",
            "primary_series": f"{scope[1]} / {metric}",
            "expected_direction": direction,
            "detected": len(primary) > 0,
            "weeks_flagged": int(len(primary)),
            "weeks_in_window": int(len(weeks)),
            "max_abs_deviation": float(primary["deviation"].abs().max()) if len(primary) else np.nan,
            "first_flagged_week": primary["week_start"].min() if len(primary) else pd.NaT,
        })
    per_event = pd.DataFrame(rows)
    n_evaluated = int((detections["status"] == "evaluated").sum())
    summary = {
        "events": len(per_event),
        "events_detected": int(per_event["detected"].sum()),
        "flags": len(flags),
        "flags_explained": int(flags["explained_by"].notna().sum()),
        "flags_unexplained": int(flags["explained_by"].isna().sum()),
        "evaluated_series_weeks": n_evaluated,
        "flag_rate_pct": (len(flags) * 100.0 / n_evaluated) if n_evaluated else None,
        "unexplained_rate_pct": (int(flags["explained_by"].isna().sum()) * 100.0 / n_evaluated) if n_evaluated else None,
    }
    return {"per_event": per_event, "flags": flags, "summary": summary}


def sensitivity(df, labels, thresholds=(3.5, 4.0, 4.5, 5.0, 6.0, 8.0), base=None):
    """How the results change with the threshold. A sensitivity analysis, NOT a tuning step."""
    base = base or ad.DetectionParams()
    rows = []
    for threshold in thresholds:
        params = ad.DetectionParams(window_weeks=base.window_weeks, min_history_weeks=base.min_history_weeks,
                                    threshold=threshold, severity_bands=(max(threshold, base.severity_bands[0]),
                                                                          max(threshold, base.severity_bands[0]) + 1))
        result = evaluate(ad.detect(df, params), labels)
        s = result["summary"]
        rows.append({"threshold": threshold, "events_detected": f"{s['events_detected']} of {s['events']}",
                     "flags": s["flags"], "flag_rate_pct": s["flag_rate_pct"],
                     "unexplained_flags": s["flags_unexplained"]})
    return pd.DataFrame(rows)


def null_calibration(configs, n_series=150, length=200, relative_noise=0.09, seed=2026):
    """False-alarm rate on PURE random noise (no project data, no labels) for given parameter sets.

    Shows how often the method flags a week when, by construction, nothing unusual happened.
    ``configs`` is {name: DetectionParams}.
    """
    rng = np.random.default_rng(seed)
    noise = rng.normal(100.0, 100.0 * relative_noise, size=(n_series, length))
    index = pd.date_range("2000-01-03", periods=length, freq="7D")
    rows = []
    for name, params in configs.items():
        evaluated = flagged = 0
        for series in noise:
            scored = ad.score_series(pd.Series(series, index=index), params)
            ok = scored["status"] == "evaluated"
            evaluated += int(ok.sum())
            flagged += int(scored.loc[ok, "is_anomaly"].sum())
        rows.append({"parameters": name, "window_weeks": params.window_weeks, "threshold": params.threshold,
                     "false_alarm_rate_pct": flagged * 100.0 / evaluated})
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- report
def _table(df, floatfmt=None):
    floatfmt = floatfmt or {}
    header = "| " + " | ".join(df.columns) + " |"
    rule = "|" + "|".join("---" for _ in df.columns) + "|"
    lines = [header, rule]
    for _, row in df.iterrows():
        cells = []
        for column in df.columns:
            value = row[column]
            if isinstance(value, pd.Timestamp):
                cells.append("" if pd.isna(value) else f"{value:%Y-%m-%d}")
            elif isinstance(value, (float, np.floating)):
                cells.append("" if pd.isna(value) else floatfmt.get(column, "{:,.2f}").format(value))
            else:
                cells.append(str(value))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def build_report(detections, result, sensitivity_table, calibration_table, params):
    """Markdown evaluation report. Every number comes from this run."""
    s = result["summary"]
    per_event = result["per_event"].copy()
    per_event["detected"] = per_event["detected"].map({True: "yes", False: "NO"})
    flags = result["flags"]
    unexplained = flags[flags["explained_by"].isna()].copy()
    unexplained["month"] = unexplained["week_start"].dt.strftime("%Y-%m")
    by_month = unexplained.groupby("month").size().rename("unexplained_flags").reset_index()
    top = unexplained.head(10)[["scope_type", "scope_value", "metric", "week_start", "observed",
                                "baseline", "deviation", "direction"]]
    status_counts = detections["status"].value_counts().rename_axis("status").reset_index(name="series_weeks")
    series_count = detections.groupby(["scope_type", "scope_value", "metric"]).ngroups
    weeks = detections["week_start"].nunique()

    parts = [
        "# Anomaly Detection Evaluation",
        "",
        "> **Synthetic data.** The four anomalies below were planted by `src/data_generator.py`. "
        "This report is generated by `src/anomaly_evaluation.py`; every number comes from the pipeline run.",
        "> A flag is a **statistical anomaly** (unusual compared with the previous weeks). It is **not** a confirmed "
        "business issue and it is **not** a forecast.",
        "",
        "## Method",
        "",
        f"- Weekly grain (Monday to Sunday), complete weeks only: {weeks} weeks scored.",
        f"- {series_count} series: revenue, profit and profit margin % for the whole business and for each region, "
        "category and customer segment.",
        f"- Baseline = median of the previous **{params.window_weeks}** complete weeks (earlier weeks only). "
        f"Spread = 1.4826 x median absolute deviation of the same weeks. Deviation = (observed - baseline) / spread.",
        f"- Flagged when |deviation| >= **{params.threshold}**. Fewer than {params.min_history_weeks} valid earlier weeks: "
        "not scored. Zero spread: not scored.",
        f"- Severity: moderate below {params.severity_bands[0]:g}, high from {params.severity_bands[0]:g}, "
        f"extreme from {params.severity_bands[1]:g}.",
        "",
        "### Series-weeks by status",
        "",
        _table(status_counts),
        "",
        "## Parameter history (full disclosure)",
        "",
        "1. The first parameters (8-week window, threshold 3.5) were fixed **before** the detector was run on the data.",
        "2. On the project data they flagged far too many weeks. A simulation on **pure random noise** (no project data, "
        "no labels) showed the same: they flag about 1 week in 20 even when nothing unusual happens.",
        "3. The window and threshold were revised using **only that noise simulation**, aiming for about 1% false alarms "
        "on noise. The planted labels were not used to choose them.",
        "",
        _table(calibration_table, {"false_alarm_rate_pct": "{:.2f}"}),
        "",
        "Real weekly data flags more often than pure noise because it also has trend and seasonality, "
        "which a rolling baseline cannot anticipate.",
        "",
        "## Planted anomalies: detected?",
        "",
        _table(per_event, {"max_abs_deviation": "{:,.1f}"}),
        "",
        f"**{s['events_detected']} of {s['events']} planted events detected.** With only {s['events']} events this is an "
        "anecdote, not an accuracy estimate.",
        "",
        "## All flags",
        "",
        "| Item | Count |",
        "|---|---:|",
        f"| Series-weeks scored | {s['evaluated_series_weeks']:,} |",
        f"| Flags | {s['flags']:,} ({s['flag_rate_pct']:.2f}% of scored) |",
        f"| Flags inside a planted event window (explained) | {s['flags_explained']:,} |",
        f"| Flags matching no planted event (unexplained) | {s['flags_unexplained']:,} ({s['unexplained_rate_pct']:.2f}% of scored) |",
        "",
        "Unexplained flags are false alarms with respect to the planted events, but some reflect real patterns the "
        "generator built in (seasonal peaks, regional growth), which a simple rolling baseline cannot anticipate.",
        "",
        "### Unexplained flags by month",
        "",
        _table(by_month) if len(by_month) else "None.",
        "",
        "### Ten strongest unexplained flags",
        "",
        _table(top, {"observed": "{:,.2f}", "baseline": "{:,.2f}", "deviation": "{:,.1f}"}) if len(top) else "None.",
        "",
        "## Sensitivity to the threshold",
        "",
        "How the results change if the threshold changes. This is a **sensitivity analysis**, not a tuning step: "
        "the threshold in use was fixed by the noise calibration above.",
        "",
        _table(sensitivity_table, {"threshold": "{:g}", "flag_rate_pct": "{:.2f}"}),
        "",
        "## Limitations",
        "",
        "- Only 4 planted events and one synthetic dataset: results do not generalise to real businesses.",
        "- 39 series are tested every week, so some false alarms are expected by chance alone.",
        "- A rolling baseline cannot know about seasonal peaks or planned promotions, so such weeks can be flagged.",
        "- Slow, steady drifts are absorbed into the baseline and are not flagged.",
        "- A flag says *where to look*. It does not say why. Causes need evidence from outside this dataset.",
        "- Nothing here is a forecast. No predictive capability is claimed.",
    ]
    return "\n".join(parts) + "\n"


def run(df, labels=None, params=None, report_path=config.ANOMALY_EVALUATION_REPORT):
    """Detect, score against the planted labels, write the report. Returns (detections, result)."""
    params = params or ad.DetectionParams()
    labels = labels if labels is not None else load_labels()
    detections = ad.detect(df, params)
    result = evaluate(detections, labels)
    sens = sensitivity(df, labels, base=params)
    calibration = null_calibration({
        "first choice (before any run)": ad.DetectionParams(window_weeks=8, min_history_weeks=6, threshold=3.5,
                                                              severity_bands=(5.0, 8.0)),
        "in use": params,
    })
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(build_report(detections, result, sens, calibration, params), encoding="utf-8")
    return detections, result
