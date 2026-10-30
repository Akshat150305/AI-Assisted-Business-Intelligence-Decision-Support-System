"""Rule-based insight engine: turns measured KPIs into ranked, evidence-backed findings.

How it works, in four sentences
--------------------------------
1. Every rule looks at numbers computed by the pipeline (src/metrics.py, src/anomaly_detection.py) for a fixed
   current window and a baseline, and fires only when a documented threshold in app_config/config.py is reached.
2. A finding separates FACT (what was measured), POSSIBLE EXPLANATION (hypotheses, never conclusions),
   RECOMMENDED ACTION (something to check or decide) and EVIDENCE NEEDED (what would confirm or reject the
   explanation) - requirements FR-22 and FR-23.
3. Findings are ranked by ``priority_score`` = the profit at stake as a % of the current window's profit, so
   a revenue change, a margin gap and an anomaly are compared in the same unit (FR-24). Ties: higher
   confidence first, then finding_id.
4. All numbers are formatted from computed values; no result is typed in (FR-25). Nothing here predicts the
   future, and no action is promised to produce a result.

Time windows (all derived from the data, never hard-coded dates)
----------------------------------------------------------------
* latest quarter    = the last COMPLETE calendar quarter in the data, compared with the quarter before it.
* current window    = the last 4 complete quarters (12 months) - or fewer if the data is shorter.
* prior window      = the 4 quarters before that, when the data has them (year-on-year comparison).

"Profit at stake" is arithmetic, not a forecast: for a margin gap it is (gap in percentage points) x revenue / 100,
for a change it is the profit effect of the change, for a growth gap the profit on the revenue above/below
what the whole business growth implies.

No planted-anomaly labels are used here (tests enforce it). ALL DATA IS SYNTHETIC.
"""
import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app_config import config
from src import anomaly_detection, metrics
from src.filters import SalesFilter

FINDING_COLUMNS = [
    "rank", "finding_id", "rule", "finding_type", "title", "metric", "scope", "period", "baseline_period",
    "observed_value", "baseline_value", "absolute_change", "pct_change", "unit", "priority_score",
    "profit_impact", "confidence", "evidence", "observed_fact", "possible_explanation",
    "recommended_action", "evidence_needed", "limitations",
]
FINDING_TYPES = ["change", "strength", "weakness", "regional", "anomaly", "status"]
CONFIDENCE_ORDER = {"High": 0, "Medium": 1, "Low": 2}
BASE_LIMITATION = "Synthetic data: describes the simulated business only, never a real company."


@dataclass(frozen=True)
class FindingParams:
    material_change_pct: float = config.FINDING_MATERIAL_CHANGE_PCT
    margin_shift_pp: float = config.FINDING_MARGIN_SHIFT_PP
    category_gap_pp: float = config.FINDING_CATEGORY_GAP_PP
    product_gap_pp: float = config.FINDING_PRODUCT_GAP_PP
    region_margin_gap_pp: float = config.FINDING_REGION_MARGIN_GAP_PP
    region_growth_gap_pp: float = config.FINDING_REGION_GROWTH_GAP_PP
    min_share_pct: float = config.FINDING_MIN_SHARE_PCT
    high_confidence_orders: int = config.FINDING_HIGH_CONFIDENCE_ORDERS
    medium_confidence_orders: int = config.FINDING_MEDIUM_CONFIDENCE_ORDERS
    anomaly_lookback_weeks: int = config.FINDING_ANOMALY_LOOKBACK_WEEKS

    def __post_init__(self):
        for name in ("material_change_pct", "margin_shift_pp", "category_gap_pp", "product_gap_pp",
                     "region_margin_gap_pp", "region_growth_gap_pp"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.min_share_pct < 0 or self.anomaly_lookback_weeks < 1:
            raise ValueError("min_share_pct must be >= 0 and anomaly_lookback_weeks >= 1")
        if self.medium_confidence_orders > self.high_confidence_orders:
            raise ValueError("medium_confidence_orders must not exceed high_confidence_orders")


# ----------------------------------------------------------------------------- formatting helpers
def money(x):
    return f"{x:,.0f}"


def pct(x, signed=True):
    return f"{x:+.1f}%" if signed else f"{x:.1f}%"


def pp(x, signed=True):
    return f"{x:+.1f} pp" if signed else f"{x:.1f} pp"


def slug(text):
    return re.sub(r"[^A-Za-z0-9]+", "-", str(text)).strip("-")


def confidence_from_orders(orders, params):
    if orders >= params.high_confidence_orders:
        return "High"
    return "Medium" if orders >= params.medium_confidence_orders else "Low"


def downgrade(level):
    return {"High": "Medium", "Medium": "Low", "Low": "Low"}[level]


def kpis(df, window, **flt):
    start, end = window
    return metrics.compute_kpis(df, SalesFilter(start_date=start, end_date=end, **flt))


def label(window):
    return f"{window[0]} to {window[1]}"


# ----------------------------------------------------------------------------- time windows
def complete_quarters(df):
    """Calendar quarters whose first and last day both lie inside the data's date range."""
    if df.empty:
        return []
    low, high = df["order_date"].min().normalize(), df["order_date"].max().normalize()
    return [q for q in pd.period_range(pd.Period(low, "Q"), pd.Period(high, "Q"), freq="Q")
            if q.start_time.normalize() >= low and q.end_time.normalize() <= high]


def _window(quarters):
    return (quarters[0].start_time.strftime("%Y-%m-%d"), quarters[-1].end_time.strftime("%Y-%m-%d"))


def analysis_windows(df):
    """The periods the rules compare. Missing comparisons are None (the rules that need them are skipped)."""
    quarters = complete_quarters(df)
    out = {"quarters": quarters, "latest_q": None, "prev_q": None, "same_q_ly": None, "prev_q_ly": None,
           "current": None, "prior": None, "n_quarters": 0}
    if not quarters:
        return out
    have = set(quarters)
    latest = quarters[-1]
    out["latest_q"] = _window([latest])
    out["latest_q_name"] = f"{latest.year}-Q{latest.quarter}"
    if latest - 1 in have:
        out["prev_q"] = _window([latest - 1])
        out["prev_q_name"] = f"{(latest - 1).year}-Q{(latest - 1).quarter}"
    if latest - 4 in have and latest - 5 in have:
        out["same_q_ly"], out["prev_q_ly"] = _window([latest - 4]), _window([latest - 5])
    n = min(4, len(quarters))
    current = quarters[-n:]
    out["n_quarters"] = n
    out["current"] = _window(current)
    prior = [q - n for q in current]
    if all(q in have for q in prior):
        out["prior"] = _window(prior)
    return out


# ----------------------------------------------------------------------------- building a finding
def make_finding(rule, finding_type, subject, title, metric, scope, period, baseline_period, observed, baseline,
                 unit, profit_impact, confidence, evidence, fact, explanation, action, needed, limitations,
                 pct_change=None, total_profit=None):
    absolute = None if observed is None or baseline is None else observed - baseline
    score = 0.0 if not total_profit or total_profit <= 0 else round(100.0 * profit_impact / total_profit, 2)
    return {
        "rank": None, "finding_id": f"F-{rule}-{slug(subject)}", "rule": rule, "finding_type": finding_type,
        "title": title, "metric": metric, "scope": scope, "period": period, "baseline_period": baseline_period,
        "observed_value": observed, "baseline_value": baseline, "absolute_change": absolute,
        "pct_change": pct_change, "unit": unit, "priority_score": score, "profit_impact": round(float(profit_impact), 2),
        "confidence": confidence, "evidence": list(evidence), "observed_fact": fact,
        "possible_explanation": explanation, "recommended_action": action, "evidence_needed": needed,
        "limitations": " ".join([BASE_LIMITATION, *limitations]),
    }


def _driver_line(df, cur_window, prev_window, by, metric, label_by):
    """Which category/region moved the metric most between two windows (absolute change and share of the total change)."""
    cur = metrics.breakdown(df, by, SalesFilter(start_date=cur_window[0], end_date=cur_window[1]))
    prev = metrics.breakdown(df, by, SalesFilter(start_date=prev_window[0], end_date=prev_window[1]))
    if cur.empty or prev.empty:
        return None
    key = {"category": "category", "region": "region"}[by]
    cur_values = cur.set_index(key)[metric].rename("cur")
    prev_values = prev.set_index(key)[metric].rename("prev")
    joined = pd.concat([cur_values, prev_values], axis=1, join="outer").fillna(0.0)    # no join suffixes (deprecated in pandas 3.1)
    joined["delta"] = joined["cur"] - joined["prev"]
    total = joined["delta"].sum()
    if total == 0:
        return None
    top = joined["delta"].abs().idxmax()
    return (f"Largest {label_by} contributor to the {metric} change: {top} "
            f"({joined.loc[top, 'delta']:+,.0f}, {100.0 * joined.loc[top, 'delta'] / total:.0f}% of the total change).")


def _movement_evidence(cur, prev):
    lines = []
    for key, name in (("distinct_orders", "Distinct orders"), ("avg_order_value", "Average order value"),
                      ("units_sold", "Units sold"), ("active_customers", "Active customers")):
        change = metrics.pct_change(cur[key], prev[key])
        if change is not None:
            lines.append(f"{name}: {cur[key]:,.0f} vs {prev[key]:,.0f} ({pct(change)})" if key != "avg_order_value"
                         else f"{name}: {cur[key]:,.2f} vs {prev[key]:,.2f} ({pct(change)})")
    margin = (cur["profit_margin_pct"], prev["profit_margin_pct"])
    if None not in margin:
        lines.append(f"Profit margin: {margin[0]:.2f}% vs {margin[1]:.2f}% ({pp(margin[0] - margin[1])})")
    return lines


def _volume_or_value(cur, prev):
    orders = metrics.pct_change(cur["distinct_orders"], prev["distinct_orders"])
    aov = metrics.pct_change(cur["avg_order_value"], prev["avg_order_value"])
    if orders is None or aov is None:
        return None
    return "mainly the NUMBER of orders" if abs(orders) >= abs(aov) else "mainly the VALUE of each order"


# ----------------------------------------------------------------------------- rules
def rule_quarter_change(df, w, p, total_profit):
    """Latest complete quarter vs the quarter before. Checks whether the same move happened a year earlier (seasonality)."""
    if not (w["latest_q"] and w["prev_q"]):
        return []
    cur, prev = kpis(df, w["latest_q"]), kpis(df, w["prev_q"])
    findings = []
    for metric in ("revenue", "profit"):
        change = metrics.pct_change(cur[metric], prev[metric])
        if change is None or abs(change) < p.material_change_pct:
            continue
        seasonal_note, limits = None, []
        confidence = confidence_from_orders(min(cur["distinct_orders"], prev["distinct_orders"]), p)
        if w["same_q_ly"] and w["prev_q_ly"]:
            a, b = kpis(df, w["same_q_ly"]), kpis(df, w["prev_q_ly"])
            last_year = metrics.pct_change(a[metric], b[metric])
            if last_year is not None and np.sign(last_year) == np.sign(change) and abs(last_year) >= 0.5 * abs(change):
                seasonal_note = (f"The same quarter-on-quarter move happened a year earlier ({pct(last_year)} between "
                                 f"{label(w['prev_q_ly'])} and {label(w['same_q_ly'])}), which suggests a seasonal pattern.")
                confidence = downgrade(confidence)
                limits.append("The same move occurred a year earlier, so it may be normal seasonality rather than a new development.")
            else:
                seasonal_note = (f"A year earlier the same quarter-on-quarter move was {pct(last_year) if last_year is not None else 'not defined'}, "
                                 "so this move is not simply a repeat of last year's pattern.")
        else:
            limits.append("Less than two years of data: seasonality cannot be checked, so a quarter-on-quarter move may be seasonal.")
        direction = "rose" if change > 0 else "fell"
        driver = _volume_or_value(cur, prev)
        impact = abs(cur[metric] - prev[metric]) * (1.0 if metric == "profit" else (prev["profit_margin_pct"] or 0.0) / 100.0)
        evidence = [f"{metric.capitalize()}: {money(cur[metric])} in {label(w['latest_q'])} vs {money(prev[metric])} in {label(w['prev_q'])} ({pct(change)}).",
                    *_movement_evidence(cur, prev)]
        for by, name in (("category", "category"), ("region", "region")):
            line = _driver_line(df, w["latest_q"], w["prev_q"], by, metric, name)
            if line:
                evidence.append(line)
        if seasonal_note:
            evidence.append(seasonal_note)
        findings.append(make_finding(
            "QOQ", "change", f"{metric}-{w['latest_q_name']}",
            f"{metric.capitalize()} {direction} {abs(change):.1f}% in {w['latest_q_name']} vs {w['prev_q_name']}",
            metric, "All", label(w["latest_q"]), label(w["prev_q"]), cur[metric], prev[metric], "currency",
            impact, confidence, evidence,
            f"{metric.capitalize()} {direction} from {money(prev[metric])} to {money(cur[metric])} ({pct(change)}).",
            (f"The change is {driver}. " if driver else "") + "Possible causes to test: seasonal demand, promotions, price changes, "
            "new or lost customers, or a data issue. None of these can be confirmed from this data alone.",
            "Check whether the change was expected (calendar, promotions). If it was not, review the largest contributing category and region before changing forecasts or stock plans.",
            "Promotion and price calendar, stock availability, the same quarters in earlier years, a check of the data load for the period.",
            limits, pct_change=change, total_profit=total_profit))
    return findings


def rule_year_change(df, w, p, total_profit):
    """Current 12-month window vs the 12 months before it."""
    if not (w["current"] and w["prior"]):
        return []
    cur, prev = kpis(df, w["current"]), kpis(df, w["prior"])
    findings = []
    for metric in ("revenue", "profit"):
        change = metrics.pct_change(cur[metric], prev[metric])
        if change is None or abs(change) < p.material_change_pct:
            continue
        direction = "grew" if change > 0 else "declined"
        driver = _volume_or_value(cur, prev)
        impact = abs(cur[metric] - prev[metric]) * (1.0 if metric == "profit" else (prev["profit_margin_pct"] or 0.0) / 100.0)
        evidence = [f"{metric.capitalize()}: {money(cur[metric])} in {label(w['current'])} vs {money(prev[metric])} in {label(w['prior'])} ({pct(change)}).",
                    *_movement_evidence(cur, prev)]
        for by, name in (("category", "category"), ("region", "region")):
            line = _driver_line(df, w["current"], w["prior"], by, metric, name)
            if line:
                evidence.append(line)
        findings.append(make_finding(
            "YOY", "change", metric, f"{metric.capitalize()} {direction} {abs(change):.1f}% year on year",
            metric, "All", label(w["current"]), label(w["prior"]), cur[metric], prev[metric], "currency", impact,
            confidence_from_orders(min(cur["distinct_orders"], prev["distinct_orders"]), p), evidence,
            f"{metric.capitalize()} {direction} from {money(prev[metric])} to {money(cur[metric])} ({pct(change)}) over {w['n_quarters']} quarters vs the {w['n_quarters']} before.",
            (f"The change is {driver}. " if driver else "") + "Possible causes to test: customer growth, price or mix changes, promotions, "
            "or a change in how orders are recorded.",
            "Confirm which part of the change is repeatable (customers, order frequency, price) before using it in planning.",
            "Customer acquisition and retention data, price history, promotion calendar.",
            ["Two 12-month windows only; a single year-on-year comparison does not show a trend."], pct_change=change, total_profit=total_profit))
    return findings


def rule_margin(df, w, p, total_profit):
    """Profit-margin shift, year on year and quarter on quarter; a 'status' finding when the margin is holding."""
    findings = []
    comparisons = []
    if w["current"] and w["prior"]:
        comparisons.append(("MARGIN-YOY", "year on year", w["current"], w["prior"], "the whole 12-month window"))
    if w["latest_q"] and w["prev_q"]:
        comparisons.append(("MARGIN-QOQ", "quarter on quarter", w["latest_q"], w["prev_q"], "the latest quarter"))
    for rule, name, cur_w, prev_w, what in comparisons:
        cur, prev = kpis(df, cur_w), kpis(df, prev_w)
        if cur["profit_margin_pct"] is None or prev["profit_margin_pct"] is None:
            continue
        shift = cur["profit_margin_pct"] - prev["profit_margin_pct"]
        confidence = confidence_from_orders(min(cur["distinct_orders"], prev["distinct_orders"]), p)
        evidence = [f"Profit margin {cur['profit_margin_pct']:.2f}% in {label(cur_w)} vs {prev['profit_margin_pct']:.2f}% in {label(prev_w)} ({pp(shift)}).",
                    f"Threshold for a finding: {p.margin_shift_pp:g} percentage points."]
        if abs(shift) >= p.margin_shift_pp:
            direction = "improved" if shift > 0 else "weakened"
            findings.append(make_finding(
                rule, "change", name.replace(" ", "-"), f"Profit margin {direction} {abs(shift):.1f} pp {name}",
                "profit_margin_pct", "All", label(cur_w), label(prev_w), cur["profit_margin_pct"], prev["profit_margin_pct"],
                "percentage points", abs(shift) * cur["revenue"] / 100.0, confidence, evidence,
                f"Profit margin {direction} from {prev['profit_margin_pct']:.2f}% to {cur['profit_margin_pct']:.2f}% ({pp(shift)}) for {what}.",
                "Possible causes to test: a shift in product mix, changes in unit cost or discounting, or a cost-data error.",
                "Review margin by category and product for the same periods to see where the shift comes from.",
                "Unit cost history, discount records, product mix by period.",
                [] if rule == "MARGIN-YOY" else ["A one-quarter margin move can be seasonal mix."],
                pct_change=metrics.pct_change(cur["profit_margin_pct"], prev["profit_margin_pct"]), total_profit=total_profit))
        elif rule == "MARGIN-YOY":
            findings.append(make_finding(
                rule, "status", "stable", f"Profit margin is holding ({pp(shift)} year on year)",
                "profit_margin_pct", "All", label(cur_w), label(prev_w), cur["profit_margin_pct"], prev["profit_margin_pct"],
                "percentage points", 0.0, confidence, evidence,
                f"Profit margin was {cur['profit_margin_pct']:.2f}% against {prev['profit_margin_pct']:.2f}% a year earlier ({pp(shift)}), inside the {p.margin_shift_pp:g} pp threshold.",
                "No material shift to explain.", "No action needed; keep monitoring on the dashboard.",
                "None.", ["A stable total can hide opposite moves in categories or regions."],
                pct_change=metrics.pct_change(cur["profit_margin_pct"], prev["profit_margin_pct"]), total_profit=total_profit))
    return findings


def rule_category_margin(df, w, p, total_profit):
    """Categories whose margin is far above (strength) or below (weakness) the whole-business margin."""
    if not w["current"]:
        return []
    window = w["current"]
    total = kpis(df, window)
    if total["profit_margin_pct"] is None:
        return []
    table = metrics.breakdown(df, "category", SalesFilter(start_date=window[0], end_date=window[1]))
    findings = []
    for _, r in table.iterrows():
        if r["revenue_share_pct"] < p.min_share_pct or pd.isna(r["profit_margin_pct"]):
            continue
        gap = r["profit_margin_pct"] - total["profit_margin_pct"]
        if abs(gap) < p.category_gap_pp:
            continue
        weak = gap < 0
        products = metrics.breakdown(df, "product", SalesFilter(start_date=window[0], end_date=window[1], category=r["category"]))
        worst = products.sort_values("profit_margin_pct").iloc[0] if not products.empty else None
        evidence = [f"{r['category']} margin {r['profit_margin_pct']:.2f}% vs {total['profit_margin_pct']:.2f}% for the whole business ({pp(gap)}).",
                    f"{r['category']} is {r['revenue_share_pct']:.1f}% of revenue but {r['profit_share_pct']:.1f}% of profit.",
                    f"Revenue {money(r['revenue'])}, profit {money(r['profit'])}, {r['distinct_orders']:,.0f} distinct orders in {label(window)}."]
        if worst is not None:
            evidence.append(f"Lowest-margin product in the category: {worst['product_name']} ({worst['profit_margin_pct']:.1f}%).")
        findings.append(make_finding(
            "CATMARGIN", "weakness" if weak else "strength", r["category"],
            f"{r['category']} margin is {abs(gap):.1f} pp {'below' if weak else 'above'} the business average",
            "profit_margin_pct", r["category"], label(window), "whole business, same period", r["profit_margin_pct"],
            total["profit_margin_pct"], "percentage points", abs(gap) * r["revenue"] / 100.0,
            confidence_from_orders(r["distinct_orders"], p), evidence,
            f"{r['category']} earns {r['profit_margin_pct']:.1f}% margin against {total['profit_margin_pct']:.1f}% overall, on {r['revenue_share_pct']:.1f}% of revenue.",
            ("Possible causes to test: high unit costs, heavy discounting, competitive pricing or a mix of low-margin products. "
             if weak else "Possible causes to test: premium pricing, low unit costs or a favourable product mix. ")
            + "Profit at stake is the arithmetic gap, not a forecast.",
            ("Review pricing, discounts and supplier costs for the lowest-margin products in the category before changing the range."
             if weak else "Check whether the high margin is repeatable and whether the category can be given more visibility without lowering its margin."),
            "Unit cost and discount history, competitor prices, product-level volumes.",
            ["Margin gap is measured on one 12-month window."], pct_change=None, total_profit=total_profit))
    return findings


def rule_product_outliers(df, w, p, total_profit):
    """Products whose margin differs from their own category's margin by at least the threshold."""
    if not w["current"]:
        return []
    window = w["current"]
    revenue_total = kpis(df, window)["revenue"]
    products = metrics.breakdown(df, "product", SalesFilter(start_date=window[0], end_date=window[1]))
    categories = metrics.breakdown(df, "category", SalesFilter(start_date=window[0], end_date=window[1])).set_index("category")
    findings = []
    for _, r in products.iterrows():
        if r["revenue_share_pct"] < p.min_share_pct or pd.isna(r["profit_margin_pct"]):
            continue
        cat_margin = categories.loc[r["category"], "profit_margin_pct"]
        gap = r["profit_margin_pct"] - cat_margin
        if abs(gap) < p.product_gap_pp:
            continue
        weak = gap < 0
        findings.append(make_finding(
            "PRODUCT", "weakness" if weak else "strength", r["product_name"],
            f"{r['product_name']} margin is {abs(gap):.1f} pp {'below' if weak else 'above'} its category",
            "profit_margin_pct", f"{r['category']} / {r['product_name']}", label(window), f"{r['category']} category, same period",
            r["profit_margin_pct"], cat_margin, "percentage points", abs(gap) * r["revenue"] / 100.0,
            confidence_from_orders(r["distinct_orders"], p),
            [f"{r['product_name']} margin {r['profit_margin_pct']:.2f}% vs {cat_margin:.2f}% for {r['category']} ({pp(gap)}).",
             f"Revenue {money(r['revenue'])} ({r['revenue_share_pct']:.1f}% of total), profit {money(r['profit'])}, {r['distinct_orders']:,.0f} distinct orders.",
             f"Average order value {r['avg_order_value']:,.2f}."],
            f"{r['product_name']} earns {r['profit_margin_pct']:.1f}% margin; its category averages {cat_margin:.1f}%.",
            ("Possible causes to test: a high unit cost, a price set too low, or discounting. " if weak
             else "Possible causes to test: a strong price position or a low unit cost. ") + "Profit at stake is the arithmetic gap, not a forecast.",
            ("Review the price and cost of this product against similar items." if weak
             else "Check whether the price or cost advantage can be kept and whether similar products could follow it."),
            "Unit cost, price and discount history for this product and its peers.",
            ["Single 12-month window; one product's margin can move with a few large orders."], total_profit=total_profit))
    return findings


def rule_regions(df, w, p, total_profit):
    """Regions whose margin or growth differs materially from the whole business."""
    if not w["current"]:
        return []
    window = w["current"]
    total = kpis(df, window)
    table = metrics.breakdown(df, "region", SalesFilter(start_date=window[0], end_date=window[1]))
    prior_total = kpis(df, w["prior"]) if w["prior"] else None
    company_growth = metrics.pct_change(total["revenue"], prior_total["revenue"]) if prior_total else None
    findings = []
    for _, r in table.iterrows():
        if r["revenue_share_pct"] < p.min_share_pct or pd.isna(r["profit_margin_pct"]) or total["profit_margin_pct"] is None:
            continue
        region = r["region"]
        confidence = confidence_from_orders(r["distinct_orders"], p)
        gap = r["profit_margin_pct"] - total["profit_margin_pct"]
        if abs(gap) >= p.region_margin_gap_pp:
            findings.append(make_finding(
                "REGMARGIN", "regional", region, f"{region} margin is {abs(gap):.1f} pp {'below' if gap < 0 else 'above'} the business average",
                "profit_margin_pct", region, label(window), "whole business, same period", r["profit_margin_pct"],
                total["profit_margin_pct"], "percentage points", abs(gap) * r["revenue"] / 100.0, confidence,
                [f"{region} margin {r['profit_margin_pct']:.2f}% vs {total['profit_margin_pct']:.2f}% ({pp(gap)}).",
                 f"{region} is {r['revenue_share_pct']:.1f}% of revenue; {r['distinct_orders']:,.0f} distinct orders."],
                f"{region} earns {r['profit_margin_pct']:.1f}% margin against {total['profit_margin_pct']:.1f}% for the whole business.",
                "Possible causes to test: a different category mix in the region, regional pricing or discounting, or higher unit costs.",
                "Compare the region's category mix and discounting with the other regions before acting.",
                "Category mix by region, regional price and discount records.",
                ["Margin differences between regions can come from product mix rather than regional performance."], total_profit=total_profit))
        if prior_total and company_growth is not None:
            prior = kpis(df, w["prior"], region=region)
            growth = metrics.pct_change(r["revenue"], prior["revenue"])
            if growth is None:
                continue
            growth_gap = growth - company_growth
            if abs(growth_gap) < p.region_growth_gap_pp:
                continue
            excess = r["revenue"] - prior["revenue"] * (1.0 + company_growth / 100.0)
            lagging = growth_gap < 0
            findings.append(make_finding(
                "REGGROWTH", "regional", region,
                f"{region} revenue growth is {abs(growth_gap):.1f} pp {'below' if lagging else 'above'} the business growth",
                "revenue", region, label(window), label(w["prior"]), r["revenue"], prior["revenue"], "currency",
                abs(excess) * r["profit_margin_pct"] / 100.0,
                confidence_from_orders(min(r["distinct_orders"], prior["distinct_orders"]), p),
                [f"{region} revenue {money(r['revenue'])} vs {money(prior['revenue'])} ({pct(growth)}); whole business {pct(company_growth)}.",
                 f"Revenue {'below' if lagging else 'above'} what business-wide growth would imply: {money(abs(excess))}."],
                f"{region} revenue changed {pct(growth)} year on year while the whole business changed {pct(company_growth)}.",
                "Possible causes to test: local demand, regional promotions, customer wins or losses, or stock availability.",
                ("Review customer and order trends in the region to see whether the shortfall comes from fewer customers, fewer orders or smaller orders."
                 if lagging else "Find out what drove the outperformance (customers, orders, categories) and whether it can be repeated elsewhere."),
                "Customer counts and order frequency by region and period; regional promotion and stock records.",
                ["Growth gap compares two 12-month windows; a few large customers can move a region's growth."], pct_change=growth, total_profit=total_profit))
    return findings


def rule_anomalies(df, detections, w, p, total_profit):
    """Recent high/extreme anomaly flags from the Phase 4 detector, one finding per scope and metric."""
    if detections is None or detections.empty or not w["current"]:
        return []
    flagged = detections[detections["is_anomaly"] & detections["severity"].isin(["high", "extreme"])]
    if flagged.empty:
        return []
    last_week = detections["week_start"].max()
    flagged = flagged[flagged["week_start"] > last_week - pd.Timedelta(weeks=p.anomaly_lookback_weeks)]
    if flagged.empty:
        return []
    weekly = anomaly_detection.weekly_metrics(df).set_index(["scope_type", "scope_value", "week_start"])["revenue"]
    margin_overall = kpis(df, w["current"])["profit_margin_pct"] or 0.0
    findings = []
    for (scope_type, scope_value, metric), group in flagged.groupby(["scope_type", "scope_value", "metric"]):
        row = group.loc[group["deviation"].abs().idxmax()]
        gap = row["observed"] - row["baseline"]
        if metric == "revenue":
            impact, unit = abs(gap) * margin_overall / 100.0, "currency"
        elif metric == "profit":
            impact, unit = abs(gap), "currency"
        else:
            impact, unit = abs(gap) / 100.0 * weekly.get((scope_type, scope_value, row["week_start"]), 0.0), "percentage points"
        high = row["direction"] == "high"
        week = f"{row['week_start']:%Y-%m-%d} to {row['week_end']:%Y-%m-%d}"
        scope = "All" if scope_type == "all" else scope_value
        findings.append(make_finding(
            "ANOMALY", "anomaly", f"{scope_value}-{metric}", f"{scope} {metric.replace('_', ' ')} unusually {'high' if high else 'low'} in week of {row['week_start']:%Y-%m-%d}",
            metric, scope, week, "median of the previous weeks", float(row["observed"]), float(row["baseline"]), unit, impact,
            "Medium" if row["severity"] == "extreme" else "Low",
            [f"Observed {row['observed']:,.2f} vs baseline {row['baseline']:,.2f} (expected range {row['expected_low']:,.2f} to {row['expected_high']:,.2f}).",
             f"Robust deviation {row['deviation']:+.1f} (flag threshold {row['threshold']:g}); severity: {row['severity']}.",
             f"{len(group)} flagged week(s) in this series within the last {p.anomaly_lookback_weeks} weeks."],
            f"{scope} {metric.replace('_', ' ')} was {row['observed']:,.2f} in the week {week}, against a baseline of {row['baseline']:,.2f} ({row['deviation']:+.1f} robust deviations).",
            "Starting points for the investigation: " + str(row["suggested_check"]),
            "Treat as a statistical anomaly to investigate, not a confirmed issue: check the source orders for that week.",
            "The orders behind the flagged week, promotion/price calendar, data-load records for the week.",
            ["The detector's rolling baseline cannot anticipate seasonal peaks, so seasonal weeks can be flagged without any problem."],
            total_profit=total_profit))
    return findings


# ----------------------------------------------------------------------------- the engine
def empty_findings():
    return pd.DataFrame(columns=FINDING_COLUMNS)


def generate_findings(df, detections=None, params=None):
    """Run every rule and return the ranked findings (one row per finding, FINDING_COLUMNS).

    ``df`` is the cleaned sales table; ``detections`` is the output of anomaly_detection.detect(df) (optional).
    """
    params = params or FindingParams()
    if df is None or df.empty:
        return empty_findings()
    w = analysis_windows(df)
    if not w["current"]:
        return empty_findings()                       # less than one complete quarter of data
    total_profit = kpis(df, w["current"])["profit"]
    rows = []
    for rule in (rule_quarter_change, rule_year_change, rule_margin, rule_category_margin, rule_product_outliers, rule_regions):
        rows += rule(df, w, params, total_profit)
    rows += rule_anomalies(df, detections, w, params, total_profit)
    if not rows:
        return empty_findings()
    out = pd.DataFrame(rows)
    out["_conf"] = out["confidence"].map(CONFIDENCE_ORDER)
    out = out.sort_values(["priority_score", "_conf", "finding_id"], ascending=[False, True, True], kind="stable").reset_index(drop=True)
    out["rank"] = np.arange(1, len(out) + 1)
    return out[FINDING_COLUMNS]


def export_findings(findings, path=config.FINDINGS_EXPORT):
    """Flat CSV for Power BI: the evidence list is joined with ' | '."""
    path.parent.mkdir(parents=True, exist_ok=True)
    out = findings.copy()
    out["evidence"] = out["evidence"].apply(lambda e: " | ".join(e) if isinstance(e, (list, tuple)) else e)
    out.round({"observed_value": 4, "baseline_value": 4, "absolute_change": 4, "pct_change": 4, "profit_impact": 2}).to_csv(path, index=False)
    return len(out)


# ----------------------------------------------------------------------------- report
def build_report(findings, windows, params=None, top_n=10):
    params = params or FindingParams()
    lines = ["# Findings summary", "",
             "_Generated by `python run_pipeline.py` (src/recommendations.py). Do not edit by hand._", "",
             "> **Synthetic data.** These findings describe a simulated retail business, never a real company. "
             "Possible explanations are **hypotheses** that need the evidence listed with each finding. "
             "Recommended actions are things to check or decide, not promises of a result.", ""]
    if findings.empty:
        lines += ["No finding reached a threshold in this data.", ""]
    else:
        counts = findings["finding_type"].value_counts().to_dict()
        lines += ["## Overview", "",
                  f"- Analysis window (current): {label(windows['current'])} ({windows['n_quarters']} complete quarters)."
                  + (f" Baseline: {label(windows['prior'])}." if windows["prior"] else " No prior window: year-on-year rules skipped."),
                  f"- Latest quarter: {windows.get('latest_q_name', 'n/a')}"
                  + (f" compared with {windows['prev_q_name']}." if windows["prev_q"] else "."),
                  f"- {len(findings)} findings: " + ", ".join(f"{n} {t}" for t, n in counts.items()) + ".", ""]
        lines += [f"## Top {min(top_n, len(findings))} findings", ""]
        for _, f in findings.head(top_n).iterrows():
            lines += [f"### {f['rank']}. {f['title']}", "",
                      f"`{f['finding_id']}` | type: {f['finding_type']} | confidence: **{f['confidence']}** | "
                      f"profit at stake: {money(f['profit_impact'])} ({f['priority_score']:.2f}% of current-window profit)", "",
                      f"- **Observed fact:** {f['observed_fact']}",
                      f"- **Possible explanation (hypothesis):** {f['possible_explanation']}",
                      f"- **Recommended action:** {f['recommended_action']}",
                      f"- **Evidence needed to validate:** {f['evidence_needed']}",
                      f"- **Limitations:** {f['limitations']}",
                      "- **Evidence:**", *[f"  - {e}" for e in f["evidence"]], ""]
        lines += ["## All findings", "", "| Rank | ID | Type | Confidence | Priority (% of profit) | Title |", "|---|---|---|---|---|---|"]
        lines += [f"| {r['rank']} | `{r['finding_id']}` | {r['finding_type']} | {r['confidence']} | {r['priority_score']:.2f} | {r['title']} |"
                  for _, r in findings.iterrows()]
        lines.append("")
    lines += ["## Rules and thresholds", "",
              "| Rule | Fires when | Threshold |", "|---|---|---|",
              f"| QOQ | latest complete quarter vs previous quarter, revenue or profit | change of {params.material_change_pct:g}% or more |",
              f"| YOY | last 12 months vs the 12 months before, revenue or profit | change of {params.material_change_pct:g}% or more |",
              f"| MARGIN | profit margin shift (year on year, quarter on quarter); a status finding if year on year is inside the threshold | {params.margin_shift_pp:g} pp |",
              f"| CATMARGIN | category margin vs whole-business margin | {params.category_gap_pp:g} pp, share of revenue at least {params.min_share_pct:g}% |",
              f"| PRODUCT | product margin vs its own category's margin | {params.product_gap_pp:g} pp, share of revenue at least {params.min_share_pct:g}% |",
              f"| REGMARGIN | region margin vs whole-business margin | {params.region_margin_gap_pp:g} pp |",
              f"| REGGROWTH | region year-on-year growth vs whole-business growth | {params.region_growth_gap_pp:g} pp |",
              f"| ANOMALY | high or extreme anomaly flag (Phase 4) in the last {params.anomaly_lookback_weeks} weeks | detector threshold |", "",
              f"Confidence: High if the comparison rests on at least {params.high_confidence_orders:,} distinct orders in each group, "
              f"Medium from {params.medium_confidence_orders:,}, otherwise Low. It is lowered one level when the same quarter-on-quarter move "
              "happened a year earlier (seasonality). Anomaly findings are Medium at most.", "",
              "Priority = profit at stake as a % of the current window's profit, so a revenue change, a margin gap and an anomaly share one scale.", "",
              "## Limitations", "",
              "- The thresholds are judgement calls fixed before the rules ran; different thresholds give different findings.",
              "- Findings are descriptive. They do not forecast and they do not establish causes.",
              "- With a rolling baseline, seasonal weeks can be flagged as anomalies without any problem.",
              "- Several findings can describe the same underlying fact from different angles (for example a weak category and a weak product inside it).", ""]
    return "\n".join(lines)


def run(df, detections=None, params=None, export_path=config.FINDINGS_EXPORT, report_path=config.FINDINGS_REPORT):
    params = params or FindingParams()
    if detections is None:
        detections = anomaly_detection.detect(df)
    findings = generate_findings(df, detections, params)
    export_findings(findings, export_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(build_report(findings, analysis_windows(df), params), encoding="utf-8")
    return findings
