"""The Power BI specification, written as data so that tests can check it against the real exports.

Why code and not just a Markdown file?  A specification that names a column which does not exist, or a
measure that is not defined, wastes hours when you build the dashboard. Here
  * every  table[column]  used in a DAX measure, relationship or visual must exist in the exported CSV files,
  * every  [Measure]  must be defined,
  * every relationship must be valid on the real data (unique "one" side, no orphan keys),
  * the Markdown files in powerbi/ are GENERATED from this module and a test fails if they are stale,
  * the star schema is replayed in pandas to prove it reproduces the Phase 3 KPIs under filters.

IMPORTANT HONESTY NOTE
----------------------
The DAX below has NOT been executed by Power BI in this workspace (Power BI Desktop is a Windows
application). What is verified here is everything around it: the fields exist, the relationships hold, and the
numbers the DAX must produce are computed independently by SQL/Python (the "answer key"). The dashboard counts as
built and validated only after YOU tick off powerbi/build_guide.md section 8 in Power BI Desktop.

Regenerate the documents:   python -m src.powerbi_spec      (run_pipeline.py does it too)
ALL DATA IS SYNTHETIC.
"""
import re

import pandas as pd

from app_config import config
from src import metrics
from src.filters import SalesFilter

POWERBI_DIR = config.PROJECT_ROOT / "powerbi"

# ============================================================================= DATA MODEL
# type names are Power BI types: text, whole, decimal, date, true/false
MODEL = {
    "fact_sales": {
        "role": "Fact table. One row = one order line. Key = (order_id, line_number).",
        "columns": {"order_id": "text", "line_number": "whole", "order_date": "date", "customer_id": "text",
                    "product_id": "text", "quantity": "whole", "unit_price": "decimal", "revenue": "decimal",
                    "cost": "decimal", "profit": "decimal"},
    },
    "dim_date": {
        "role": "Date table (every calendar day, no gaps). Needed for time intelligence. Mark as date table.",
        "columns": {"date": "date", "year": "whole", "quarter": "text", "month_number": "whole",
                    "month_name": "text", "year_month": "text", "month_start": "date", "week_start": "date",
                    "day_of_week_number": "whole", "day_name": "text", "is_weekend": "whole"},
    },
    "dim_product": {
        "role": "Product dimension. Holds the category.",
        "columns": {"product_id": "text", "product_name": "text", "category": "text"},
    },
    "dim_customer": {
        "role": "Customer dimension. Holds the region and the customer segment "
                "(a customer always belongs to exactly one region, so a sale's region = its customer's region).",
        "columns": {"customer_id": "text", "customer_segment": "text", "region": "text"},
    },
    "anomaly_weekly": {
        "role": "One row per scored series-week from the Phase 4 detector (scope x metric x week).",
        "columns": {"scope_type": "text", "scope_value": "text", "metric": "text", "week_start": "date",
                    "week_end": "date", "observed": "decimal", "baseline": "decimal", "spread": "decimal",
                    "expected_low": "decimal", "expected_high": "decimal", "deviation": "decimal",
                    "threshold": "decimal", "status": "text", "is_anomaly": "true/false",
                    "direction": "text", "severity": "text", "suggested_check": "text"},
    },
    "kpi_summary": {
        "role": "ANSWER KEY (SQL-computed headline KPIs, one row). Used only to check the DAX. Not on a deliverable page.",
        "columns": {"revenue": "decimal", "cost": "decimal", "profit": "decimal", "profit_margin_pct": "decimal",
                    "distinct_orders": "whole", "order_lines": "whole", "units_sold": "whole",
                    "avg_order_value": "decimal", "active_customers": "whole", "revenue_per_customer": "decimal",
                    "repeat_customer_rate": "decimal"},
    },
    "kpi_monthly": {
        "role": "ANSWER KEY (SQL-computed monthly KPIs including month-on-month % change).",
        "columns": {"month": "text", "revenue": "decimal", "cost": "decimal", "profit": "decimal",
                    "profit_margin_pct": "decimal", "distinct_orders": "whole", "order_lines": "whole",
                    "units_sold": "whole", "avg_order_value": "decimal", "revenue_change_pct": "decimal",
                    "profit_change_pct": "decimal"},
    },
    "customer_metrics": {
        "role": "ANSWER KEY / lifetime customer table (all dates). Not related to the other tables: it ignores the "
                "date slicer, so the dashboard's customer visuals are built from fact_sales instead.",
        "columns": {"customer_id": "text", "customer_segment": "text", "region": "text", "distinct_orders": "whole",
                    "order_lines": "whole", "units_sold": "whole", "revenue": "decimal", "profit": "decimal",
                    "profit_margin_pct": "decimal", "avg_order_value": "decimal", "first_order_date": "date",
                    "last_order_date": "date", "is_repeat_customer": "whole"},
    },
}

# (one-side column, many-side column, why)
RELATIONSHIPS = [
    ("dim_date[date]", "fact_sales[order_date]", "Lets the date slicer and time intelligence filter sales."),
    ("dim_product[product_id]", "fact_sales[product_id]", "Brings category and product name onto sales."),
    ("dim_customer[customer_id]", "fact_sales[customer_id]", "Brings region and segment onto sales."),
    ("dim_date[date]", "anomaly_weekly[week_start]", "Lets the date slicer filter the weeks shown on the anomaly page."),
]
SORT_BY = [("dim_date[month_name]", "dim_date[month_number]"), ("dim_date[day_name]", "dim_date[day_of_week_number]")]
DISCONNECTED = ["kpi_summary", "kpi_monthly", "customer_metrics"]
EXPORT_TABLES = list(MODEL)

# ============================================================================= DAX MEASURES
# check = how the answer key verifies it: "kpi_summary:<column>" | "anomaly" | "monthly:<column>" | None
MEASURES = [
    # ---- core
    {"name": "Revenue", "group": "Core KPIs", "format": "#,##0", "check": "kpi_summary:revenue",
     "dax": "SUM ( fact_sales[revenue] )",
     "why": "Total sales value. SUM adds the line revenues that the current filters let through."},
    {"name": "Cost", "group": "Core KPIs", "format": "#,##0", "check": "kpi_summary:cost",
     "dax": "SUM ( fact_sales[cost] )", "why": "Total cost of goods sold."},
    {"name": "Profit", "group": "Core KPIs", "format": "#,##0", "check": "kpi_summary:profit",
     "dax": "SUM ( fact_sales[profit] )",
     "why": "Revenue minus cost (the cleaning pipeline guarantees profit = revenue - cost on every line)."},
    {"name": "Profit Margin %", "group": "Core KPIs", "format": "0.00%", "check": "kpi_summary:profit_margin_pct",
     "dax": "DIVIDE ( [Profit], [Revenue] )",
     "why": "Profit per unit of revenue. A RATIO MEASURE: it must divide the two totals, never average line margins. "
            "DIVIDE returns blank instead of an error when revenue is 0. Formatted as % in Power BI (0.3668 shows as 36.68%)."},
    {"name": "Distinct Orders", "group": "Core KPIs", "format": "#,##0", "check": "kpi_summary:distinct_orders",
     "dax": "DISTINCTCOUNT ( fact_sales[order_id] )",
     "why": "Counts ORDERS, not lines (one order has several lines). Never use COUNTROWS for orders."},
    {"name": "Order Lines", "group": "Core KPIs", "format": "#,##0", "check": "kpi_summary:order_lines",
     "dax": "COUNTROWS ( fact_sales )", "why": "Number of order lines (the grain of the fact table)."},
    {"name": "Units Sold", "group": "Core KPIs", "format": "#,##0", "check": "kpi_summary:units_sold",
     "dax": "SUM ( fact_sales[quantity] )", "why": "Total quantity sold."},
    {"name": "Average Order Value", "group": "Core KPIs", "format": "#,##0.00", "check": "kpi_summary:avg_order_value",
     "dax": "DIVIDE ( [Revenue], [Distinct Orders] )",
     "why": "Revenue per ORDER (distinct orders in the denominator, not lines)."},
    # ---- customers
    {"name": "Active Customers", "group": "Customer KPIs", "format": "#,##0", "check": "kpi_summary:active_customers",
     "dax": "DISTINCTCOUNT ( fact_sales[customer_id] )",
     "why": "Customers with at least one order in the current filter context."},
    {"name": "Revenue per Customer", "group": "Customer KPIs", "format": "#,##0", "check": "kpi_summary:revenue_per_customer",
     "dax": "DIVIDE ( [Revenue], [Active Customers] )", "why": "Average revenue earned from each active customer."},
    {"name": "Repeat Customers", "group": "Customer KPIs", "format": "#,##0", "check": None,
     "dax": "COUNTROWS ( FILTER ( VALUES ( fact_sales[customer_id] ), "
            "CALCULATE ( DISTINCTCOUNT ( fact_sales[order_id] ) ) >= 2 ) )",
     "why": "VALUES gives the customers in the current filter. For each one, CALCULATE counts that customer's "
            "distinct orders (context transition); FILTER keeps those with 2 or more."},
    {"name": "Repeat Customer Rate", "group": "Customer KPIs", "format": "0.0%", "check": "kpi_summary:repeat_customer_rate",
     "dax": "DIVIDE ( [Repeat Customers], [Active Customers] )",
     "why": "Share of active customers who ordered 2+ times in the selected period. NOT a churn measure. "
            "About 97% in this synthetic data, so it does not discriminate."},
    {"name": "Top 10 Customer Revenue", "group": "Customer KPIs", "format": "#,##0", "check": None,
     "dax": "SUMX ( TOPN ( 10, VALUES ( fact_sales[customer_id] ), [Revenue], DESC ), [Revenue] )",
     "why": "TOPN picks the 10 customers with the highest revenue in the current filter; SUMX adds their revenue. "
            "If two customers tied for 10th place TOPN would return both (practically impossible with decimal revenue)."},
    {"name": "Top 10 Customer Share", "group": "Customer KPIs", "format": "0.0%", "check": None,
     "dax": "DIVIDE ( [Top 10 Customer Revenue], [Revenue] )",
     "why": "Customer concentration: the share of revenue that comes from the 10 biggest customers."},
    # ---- time intelligence
    {"name": "Revenue PM", "group": "Time intelligence", "format": "#,##0", "check": None,
     "dax": "CALCULATE ( [Revenue], DATEADD ( dim_date[date], -1, MONTH ) )",
     "why": "Revenue of the previous month. Needs dim_date to be a marked, gap-free date table."},
    {"name": "Revenue MoM %", "group": "Time intelligence", "format": "0.0%", "check": "monthly:revenue_change_pct",
     "dax": "DIVIDE ( [Revenue] - [Revenue PM], ABS ( [Revenue PM] ) )",
     "why": "(current - previous) / ABS(previous), the same rule as SQL and Python. Blank for the first month. "
            "Meaningful on a MONTH axis; do not put it on a card with a multi-month slicer."},
    {"name": "Profit PM", "group": "Time intelligence", "format": "#,##0", "check": None,
     "dax": "CALCULATE ( [Profit], DATEADD ( dim_date[date], -1, MONTH ) )", "why": "Profit of the previous month."},
    {"name": "Profit MoM %", "group": "Time intelligence", "format": "0.0%", "check": "monthly:profit_change_pct",
     "dax": "DIVIDE ( [Profit] - [Profit PM], ABS ( [Profit PM] ) )", "why": "Month-on-month change in profit (ABS rule)."},
    {"name": "Revenue PY", "group": "Time intelligence", "format": "#,##0", "check": None,
     "dax": "CALCULATE ( [Revenue], SAMEPERIODLASTYEAR ( dim_date[date] ) )",
     "why": "Revenue of the same dates one year earlier. Blank for 2024 because the data starts on 2024-01-01."},
    {"name": "Revenue YoY %", "group": "Time intelligence", "format": "0.0%", "check": None,
     "dax": "DIVIDE ( [Revenue] - [Revenue PY], ABS ( [Revenue PY] ) )",
     "why": "Year-on-year change. Works for any date range chosen with the slicer (same dates, one year back), "
            "so it is the safe change measure for cards."},
    {"name": "Profit PY", "group": "Time intelligence", "format": "#,##0", "check": None,
     "dax": "CALCULATE ( [Profit], SAMEPERIODLASTYEAR ( dim_date[date] ) )", "why": "Profit of the same dates one year earlier."},
    {"name": "Profit YoY %", "group": "Time intelligence", "format": "0.0%", "check": None,
     "dax": "DIVIDE ( [Profit] - [Profit PY], ABS ( [Profit PY] ) )", "why": "Year-on-year change in profit."},
    # ---- shares
    {"name": "Revenue Share of Selection", "group": "Shares", "format": "0.0%", "check": None,
     "dax": "DIVIDE ( [Revenue], CALCULATE ( [Revenue], ALLSELECTED () ) )",
     "why": "A row's revenue as a share of everything the user has selected with slicers (ALLSELECTED keeps slicers, "
            "removes the visual's own grouping). Shares add to 100% within one visual."},
    # ---- anomalies
    {"name": "Flagged Weeks", "group": "Anomaly Monitor", "format": "#,##0", "check": "anomaly:flags",
     "dax": "COUNTROWS ( FILTER ( anomaly_weekly, anomaly_weekly[is_anomaly] = TRUE () ) )",
     "why": "Number of series-weeks flagged as statistical anomalies. Returns 0 (not blank) when none."},
    {"name": "Scored Weeks", "group": "Anomaly Monitor", "format": "#,##0", "check": "anomaly:scored",
     "dax": "COUNTROWS ( FILTER ( anomaly_weekly, anomaly_weekly[status] = \"evaluated\" ) )",
     "why": "Series-weeks that had enough history to be scored (the denominator of the flag rate)."},
    {"name": "Flag Rate %", "group": "Anomaly Monitor", "format": "0.00%", "check": None,
     "dax": "DIVIDE ( [Flagged Weeks], [Scored Weeks] )", "why": "Share of scored series-weeks that were flagged."},
    {"name": "Extreme Flags", "group": "Anomaly Monitor", "format": "#,##0", "check": "anomaly:extreme",
     "dax": "COUNTROWS ( FILTER ( anomaly_weekly, anomaly_weekly[is_anomaly] = TRUE () "
            "&& anomaly_weekly[severity] = \"extreme\" ) )",
     "why": "Flags in the most severe band (|deviation| at or above the second severity band)."},
    {"name": "Max Abs Deviation", "group": "Anomaly Monitor", "format": "0.0", "check": "anomaly:max_dev",
     "dax": "MAXX ( FILTER ( anomaly_weekly, anomaly_weekly[is_anomaly] = TRUE () ), ABS ( anomaly_weekly[deviation] ) )",
     "why": "Largest absolute robust z-score among the flags in view. Blank when there are no flags."},
    {"name": "Anomaly Observed", "group": "Anomaly Monitor", "format": "#,##0.00", "check": None,
     "dax": "IF ( HASONEVALUE ( anomaly_weekly[scope_value] ) && HASONEVALUE ( anomaly_weekly[metric] ), "
            "MAX ( anomaly_weekly[observed] ) )",
     "why": "The week's actual value. Shown only when ONE scope and ONE metric are selected, otherwise adding a "
            "revenue value and a margin % value would be meaningless."},
    {"name": "Anomaly Baseline", "group": "Anomaly Monitor", "format": "#,##0.00", "check": None,
     "dax": "IF ( HASONEVALUE ( anomaly_weekly[scope_value] ) && HASONEVALUE ( anomaly_weekly[metric] ), "
            "MAX ( anomaly_weekly[baseline] ) )", "why": "Median of the previous weeks (prior data only)."},
    {"name": "Anomaly Expected Low", "group": "Anomaly Monitor", "format": "#,##0.00", "check": None,
     "dax": "IF ( HASONEVALUE ( anomaly_weekly[scope_value] ) && HASONEVALUE ( anomaly_weekly[metric] ), "
            "MAX ( anomaly_weekly[expected_low] ) )", "why": "Lower edge of the expected band (baseline minus threshold x spread)."},
    {"name": "Anomaly Expected High", "group": "Anomaly Monitor", "format": "#,##0.00", "check": None,
     "dax": "IF ( HASONEVALUE ( anomaly_weekly[scope_value] ) && HASONEVALUE ( anomaly_weekly[metric] ), "
            "MAX ( anomaly_weekly[expected_high] ) )", "why": "Upper edge of the expected band."},
    {"name": "Anomaly Flag Point", "group": "Anomaly Monitor", "format": "#,##0.00", "check": None,
     "dax": "IF ( [Flagged Weeks] > 0, [Anomaly Observed] )",
     "why": "Equals the observed value only in flagged weeks, so a marker series highlights them on the line chart."},
]
MEASURE_NAMES = [m["name"] for m in MEASURES]

# ============================================================================= PAGES
SLICERS = {
    "Date": "dim_date[date]", "Region": "dim_customer[region]", "Category": "dim_product[category]",
    "Segment": "dim_customer[customer_segment]",
}
SYNTHETIC_FOOTER = "SYNTHETIC DATA - generated for a portfolio project; not real business results."

PAGES = [
    {
        "name": "Executive Overview", "stakeholder": "Business manager",
        "purpose": "Is the business healthy, and what needs attention first?",
        "questions": ["Is revenue growing?", "Is profit margin holding?", "Is anything unusual right now?"],
        "slicers": ["Date", "Region", "Category", "Segment"],
        "visuals": [
            ("Cards (5)", "card", ["[Revenue]", "[Profit]", "[Profit Margin %]", "[Distinct Orders]", "[Average Order Value]"],
             "One card each. Add [Revenue YoY %] and [Profit YoY %] as the card subtitle/tooltip (blank for 2024)."),
            ("Monthly revenue and profit", "line and clustered column", ["dim_date[year_month]", "[Revenue]", "[Profit]"],
             "Columns = revenue, line = profit. X axis is text year_month so months sort correctly."),
            ("Profit margin trend", "line", ["dim_date[year_month]", "[Profit Margin %]"],
             "Start the axis at a sensible minimum and say so in the axis title; do not exaggerate small moves."),
            ("Month-on-month change", "clustered column", ["dim_date[year_month]", "[Revenue MoM %]"],
             "Negative months in a second colour. First month is blank by design."),
            ("Anomaly alert", "card", ["[Flagged Weeks]", "[Extreme Flags]"],
             "Clicking drills to the Anomaly Monitor page (button). Label: 'statistical anomalies to investigate'."),
        ],
        "notes": ["All five cards respond to every slicer.", "Footer text box on every page: " + SYNTHETIC_FOOTER],
    },
    {
        "name": "Product & Category Performance", "stakeholder": "Product/category manager",
        "purpose": "Which categories and products earn the most profit, and which have weak margin?",
        "questions": ["Which products earn the most profit?", "Which products or categories have weak margin?"],
        "slicers": ["Date", "Region", "Category", "Segment"],
        "visuals": [
            ("Revenue by category", "bar", ["dim_product[category]", "[Revenue]", "[Revenue Share of Selection]"],
             "Sorted descending. Share in the tooltip."),
            ("Profit margin by category", "bar", ["dim_product[category]", "[Profit Margin %]"],
             "Compare with the revenue bar: a big category can have a thin margin."),
            ("Top 10 products by profit", "bar", ["dim_product[product_name]", "[Profit]"],
             "Visual-level Top N filter: Top 10 by [Profit]."),
            ("Product table", "matrix", ["dim_product[category]", "dim_product[product_name]", "[Revenue]", "[Profit]",
                                          "[Profit Margin %]", "[Units Sold]"],
             "Category > product hierarchy. Conditional format margin (low = amber)."),
            ("Revenue vs margin by product", "scatter", ["dim_product[product_name]", "[Revenue]", "[Profit Margin %]", "[Profit]"],
             "X = revenue, Y = margin, size = profit. Reveals high-volume/low-margin products."),
        ],
        "notes": ["Distinct Orders is NOT additive across categories (one order can contain several categories): "
                  "do not show a Total row for it in the category matrix."],
    },
    {
        "name": "Customer Analysis", "stakeholder": "Sales manager",
        "purpose": "Which customers and segments drive revenue, and are they buying again?",
        "questions": ["Which customers and segments drive revenue?", "Are repeat purchases occurring?"],
        "slicers": ["Date", "Region", "Category", "Segment"],
        "visuals": [
            ("Cards", "card", ["[Active Customers]", "[Revenue per Customer]", "[Top 10 Customer Share]", "[Repeat Customer Rate]"],
             "Repeat rate is ~97% in this synthetic data: show it, but do not build conclusions on it."),
            ("Revenue by segment", "donut or bar", ["dim_customer[customer_segment]", "[Revenue]", "[Revenue Share of Selection]"],
             "Bar preferred when comparing; donut only for 3 segments."),
            ("Average order value by segment", "bar", ["dim_customer[customer_segment]", "[Average Order Value]"],
             "AOV = revenue / distinct orders."),
            ("Top 10 customers", "table", ["dim_customer[customer_id]", "dim_customer[customer_segment]", "dim_customer[region]",
                                           "[Revenue]", "[Distinct Orders]", "[Profit Margin %]"],
             "Visual-level Top N filter: Top 10 by [Revenue]. Built from fact_sales so the date slicer works."),
            ("Customers by region", "column", ["dim_customer[region]", "[Active Customers]"],
             "Active customers are additive across regions (each customer has one region)."),
        ],
        "notes": ["The customer_metrics table is deliberately not used here (it ignores the date slicer)."],
    },
    {
        "name": "Regional Performance", "stakeholder": "Regional manager",
        "purpose": "Is my region declining, and how does it compare with the others?",
        "questions": ["Is my region declining?", "How does it compare with the other regions?"],
        "slicers": ["Date", "Region", "Category", "Segment"],
        "visuals": [
            ("Revenue by region", "bar", ["dim_customer[region]", "[Revenue]", "[Revenue Share of Selection]"], "Sorted descending."),
            ("Monthly revenue by region", "line", ["dim_date[year_month]", "dim_customer[region]", "[Revenue]"],
             "One line per region; same colour for a region on every page."),
            ("Margin by region", "bar", ["dim_customer[region]", "[Profit Margin %]"], "Compare with revenue."),
            ("Region x category matrix", "matrix", ["dim_customer[region]", "dim_product[category]", "[Revenue]", "[Profit Margin %]"],
             "Heat-map colouring on revenue. Shows where a region's strength or weakness sits."),
            ("Region year-on-year", "table", ["dim_customer[region]", "[Revenue]", "[Revenue PY]", "[Revenue YoY %]"],
             "Blank PY for 2024 selections is expected."),
        ],
        "notes": ["Region comes from dim_customer; in this data a sale's region always equals its customer's region (tested)."],
    },
    {
        "name": "Anomaly Monitor", "stakeholder": "Business analyst",
        "purpose": "Which statistical anomalies should be investigated first, and what is the evidence?",
        "questions": ["Can I trust these numbers?", "Which anomalies should I investigate first?"],
        "slicers": ["Date"],
        "extra_slicers": ["anomaly_weekly[scope_type]", "anomaly_weekly[scope_value]", "anomaly_weekly[metric]",
                          "anomaly_weekly[severity]", "anomaly_weekly[direction]"],
        "visuals": [
            ("Cards", "card", ["[Flagged Weeks]", "[Extreme Flags]", "[Max Abs Deviation]", "[Flag Rate %]"],
             "Respond to the anomaly slicers and the date slicer."),
            ("Actual vs expected band", "line", ["anomaly_weekly[week_start]", "[Anomaly Observed]", "[Anomaly Baseline]",
                                                   "[Anomaly Expected Low]", "[Anomaly Expected High]", "[Anomaly Flag Point]"],
             "Single-select scope_value and metric slicers are REQUIRED (the measures are blank otherwise). "
             "Flag Point drawn as large red markers, no line."),
            ("Flags to investigate", "table", ["anomaly_weekly[week_start]", "anomaly_weekly[scope_value]", "anomaly_weekly[metric]",
                                               "anomaly_weekly[direction]", "anomaly_weekly[severity]", "anomaly_weekly[deviation]",
                                               "anomaly_weekly[suggested_check]"],
             "Visual filter is_anomaly = True; sort by deviation (largest absolute first, e.g. sort by severity then week)."),
            ("Flags by month", "column", ["dim_date[year_month]", "[Flagged Weeks]"], "Shows whether flags cluster in time (e.g. November seasonality)."),
            ("Data-quality note", "text box", [],
             "Text: 'A flag is a statistical anomaly to investigate, not a confirmed issue. The baseline cannot anticipate "
             "seasonality. See reports/anomaly_evaluation.md and reports/data_quality_summary.md.'"),
        ],
        "notes": ["This page does not use the region/category/segment slicers: anomaly_weekly has its own scope columns."],
    },
]

PAGE_NAMES = [p["name"] for p in PAGES]
TOKEN = re.compile(r"(\w+)\[([^\]]+)\]")          # table[column]
MEASURE_TOKEN = re.compile(r"(?<![\w\]])\[([^\]]+)\]")   # [Measure]  (not preceded by a table name)


def references(text):
    """Return ({(table, column)}, {measure names}) mentioned in a DAX/field string."""
    columns = {(t, c) for t, c in TOKEN.findall(text)}
    measures = {m for m in MEASURE_TOKEN.findall(text)}
    return columns, measures


# ============================================================================= RENDERING
def _table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c).replace("\n", " ") for c in row) + " |" for row in rows]
    return "\n".join(out)


def _status_banner():
    return ("> **Status: specification.** The DAX has not been executed by Power BI in this repository's build "
            "environment; the dashboard counts as built and validated only after the checklist in "
            "`powerbi/build_guide.md` section 8 is completed in Power BI Desktop.\n> "
            "**All data is synthetic.**\n")


def render_data_model():
    lines = ["# Power BI data model and field mapping", "", "_Generated by `python -m src.powerbi_spec`. Do not edit by hand._", "",
             _status_banner(), "## 1. Star schema", "",
             "```", "dim_date ──┐", "           ├──▶ fact_sales  (one row = one order line)", "dim_product ┤",
             "dim_customer ┘", "dim_date ──▶ anomaly_weekly (by week_start)", "```", "",
             "Why a star schema: slicers on a small dimension table filter the large fact table in one direction, "
             "DISTINCTCOUNT and time-intelligence behave predictably, and the model stays fast and easy to explain.", "",
             "## 2. Tables to load (Get Data > Text/CSV, folder `data/exports/`)", ""]
    rows = [(f"`{name}.csv`", spec["role"], len(spec["columns"])) for name, spec in MODEL.items()]
    lines += [_table(["File", "Role", "Columns"], rows), ""]
    lines += ["## 3. Columns and Power BI data types", ""]
    for name, spec in MODEL.items():
        lines += [f"### {name}", "", _table(["Column", "Power BI type"], [(f"`{c}`", t) for c, t in spec["columns"].items()]), ""]
    lines += ["## 4. Relationships (Model view)", "",
              _table(["From (one side)", "To (many side)", "Cardinality / filter direction", "Purpose"],
                     [(f"`{a}`", f"`{b}`", "One to many, single direction (one side filters many side)", why)
                      for a, b, why in RELATIONSHIPS]), "",
              "Leave `kpi_summary`, `kpi_monthly` and `customer_metrics` **without relationships** (answer key only): "
              + ", ".join(f"`{t}`" for t in DISCONNECTED) + ". Do not enable bi-directional filtering anywhere.", "",
              "## 5. Table and column settings", "",
              "- Mark `dim_date` as a date table using `dim_date[date]`.",
              *[f"- Sort `{a}` by `{b}`." for a, b in SORT_BY],
              "- Hide the raw numeric columns of `fact_sales` (revenue, cost, profit, quantity, unit_price): "
              "users should use the measures. Hide key columns used only for relationships.",
              "- Hide the three answer-key tables from the report view.",
              "- `anomaly_weekly[is_anomaly]` must have type **True/False** (Power Query normally detects it).", "",
              "## 6. Field mapping: dashboard need -> source", "",
              _table(["Dashboard field", "Comes from", "Notes"],
                     [("Region", "`dim_customer[region]`", "Customer's region = the sale's region (tested)."),
                      ("Customer segment", "`dim_customer[customer_segment]`", ""),
                      ("Category", "`dim_product[category]`", ""),
                      ("Product", "`dim_product[product_name]`", ""),
                      ("Month axis", "`dim_date[year_month]`", "Text such as 2025-03; sorts chronologically."),
                      ("Week (anomalies)", "`anomaly_weekly[week_start]`", "Monday of the week."),
                      ("Revenue / profit / orders", "`fact_sales` through measures", "See dax_measures.md.")]), ""]
    return "\n".join(lines)


def render_dax_measures():
    lines = ["# DAX measures", "", "_Generated by `python -m src.powerbi_spec`. Do not edit by hand._", "", _status_banner(),
             "## How to use this file", "",
             "1. In Power BI Desktop choose **Home > Enter data**, create an empty table named `_Measures`, load it.",
             "2. For each measure below: select `_Measures`, **New measure**, paste the DAX, set the format.",
             "3. Check each measure against the answer key (build_guide.md section 8).", "",
             "**Measure vs calculated column vs Power Query.** A *measure* is calculated at query time for the current "
             "filter context (use it for every KPI and ratio). A *calculated column* is stored per row when the model "
             "loads (use rarely, for row-level labels). A *Power Query step* reshapes data before it is loaded (already "
             "done by the Python pipeline). Ratios must be measures: an average of row-level ratios is not the ratio of totals.", ""]
    for group in dict.fromkeys(m["group"] for m in MEASURES):
        lines += [f"## {group}", ""]
        for m in (x for x in MEASURES if x["group"] == group):
            lines += [f"### {m['name']}", "", "```dax", f"{m['name']} =", f"{m['dax']}", "```", "",
                      f"- **Format:** `{m['format']}`", f"- **Why:** {m['why']}",
                      f"- **Answer key:** {_check_text(m['check'])}", ""]
    return "\n".join(lines)


def _check_text(check):
    if check is None:
        return "no direct column; verified through its parts and the filtered checks in build_guide.md"
    kind, _, col = check.partition(":")
    return {"kpi_summary": f"`kpi_summary[{col}]`", "monthly": f"`kpi_monthly[{col}]` (month by month)",
            "anomaly": "counts from `anomaly_weekly.csv` (see build_guide.md)"}[kind]


def render_dashboard_spec():
    lines = ["# Dashboard specification (5 pages)", "", "_Generated by `python -m src.powerbi_spec`. Do not edit by hand._", "",
             _status_banner(), "## Global rules", "",
             f"- Footer text box on every page: *{SYNTHETIC_FOOTER}*",
             "- Page size 16:9. One idea per visual; title every visual with the question it answers.",
             "- A category/region keeps the same colour on every page. Use colour to carry meaning, not decoration.",
             "- Slicers: " + ", ".join(f"**{k}** (`{v}`)" for k, v in SLICERS.items())
             + ". Sync the four slicers across pages 1-4 (View > Sync slicers). The Anomaly Monitor uses the date slicer and its own scope slicers.",
             "- No Total row for Distinct Orders across categories (not additive).",
             "- Percent changes use the ABS rule; blank means 'not defined' (first month, 2024 year-on-year).",
             "- Anomaly language: *statistical anomaly to investigate*, never 'problem' or 'cause'.", ""]
    for i, p in enumerate(PAGES, 1):
        lines += [f"## Page {i}: {p['name']}", "", f"**Stakeholder:** {p['stakeholder']}  ", f"**Purpose:** {p['purpose']}", "",
                  "**Questions answered:** " + " ".join(f"({n}) {q}" for n, q in enumerate(p["questions"], 1)), "",
                  "**Slicers:** " + ", ".join(f"{s} (`{SLICERS[s]}`)" for s in p["slicers"])
                  + (", " + ", ".join(f"`{s}`" for s in p.get("extra_slicers", [])) if p.get("extra_slicers") else ""), "",
                  _table(["Visual", "Type", "Fields", "Notes"],
                         [(t, k, ", ".join(f"`{f}`" for f in fields) or "-", n) for t, k, fields, n in p["visuals"]]), ""]
        if p["notes"]:
            lines += ["**Notes:**", *[f"- {n}" for n in p["notes"]], ""]
    lines += ["## Build status (fill in after building)", "",
              _table(["Page", "Built", "Slicers work", "KPIs match answer key", "Screenshot saved"],
                     [(p["name"], "[ ]", "[ ]", "[ ]", "[ ]") for p in PAGES]), "",
              "Until these boxes are ticked the dashboard must be described as *specified*, not *built*.", ""]
    return "\n".join(lines)


def validation_cases(clean_df, detections):
    """Numbers the DAX must reproduce, computed independently by the Python/SQL code (the 'answer key')."""
    filters = [
        ("No slicers", {}, SalesFilter()),
        ("Date 2025-01-01 to 2025-12-31", {"Date": "2025"}, SalesFilter(start_date="2025-01-01", end_date="2025-12-31")),
        ("Region = South", {"Region": "South"}, SalesFilter(region="South")),
        ("Category = Electronics", {"Category": "Electronics"}, SalesFilter(category="Electronics")),
        ("Segment = Corporate", {"Segment": "Corporate"}, SalesFilter(customer_segment="Corporate")),
    ]
    cases = []
    for label, _, flt in filters:
        k = metrics.compute_kpis(clean_df, flt)
        customers = metrics.customer_metrics(clean_df, flt)
        k["top10_share_pct"] = metrics.top_n_customer_share(customers, 10)
        cases.append((label, k))
    monthly = metrics.monthly_trend(clean_df)
    flagged = detections[detections["is_anomaly"]]
    anomaly = {"flags": int(len(flagged)), "scored": int((detections["status"] == "evaluated").sum()),
               "extreme": int((flagged["severity"] == "extreme").sum()),
               "max_dev": float(flagged["deviation"].abs().max())}
    return cases, monthly, anomaly


def render_build_guide(clean_df=None, detections=None):
    lines = ["# Build guide: Power BI Desktop, step by step", "", "_Generated by `python -m src.powerbi_spec`. Do not edit by hand._", "",
             _status_banner(),
             "## 1. Prepare the data", "",
             "```powershell", "cd C:\\Github_projects\\AI-Assisted-Business-Intelligence-Decision-Support-System",
             ".venv\\Scripts\\activate", "python run_pipeline.py", "```", "",
             "This creates every CSV in `data/exports/` (including `fact_sales.csv`, which is not stored in Git).", "",
             "## 2. Load the tables", "",
             "1. Power BI Desktop > **Get data > Text/CSV**. Load the files listed in data_model.md section 2 one by one (or **Get data > Folder** and expand).",
             "2. In **Power Query**: check the types against data_model.md section 3 (dates as Date, `is_anomaly` as True/False, ids as Text).",
             "3. Optional but recommended: create a Power Query parameter `ExportFolder` holding the full path of `data/exports`, "
             "and build each file path from it, so the report refreshes after `python run_pipeline.py` and moves between PCs.",
             "4. **Close & Apply**.", "",
             "## 3. Model", "",
             "1. Model view: create the four relationships in data_model.md section 4 (drag the key column from the one side to the many side).",
             "2. Check each: cardinality One to many (1:*), cross-filter direction Single.",
             "3. Mark `dim_date` as a date table. Set the sort-by columns. Hide columns as listed in data_model.md section 5.", "",
             "## 4. Measures", "",
             "Create the `_Measures` table and every measure from dax_measures.md. Set each format.", "",
             "## 5. Build the pages", "",
             "Follow dashboard_spec.md page by page. Suggested order: Executive Overview, Region, Product, Customer, Anomaly Monitor. "
             "Add the four slicers once, then **Sync slicers** across pages 1-4.", "",
             "## 6. Interactions and polish", "",
             "- Edit interactions so the four cards on a page are not cross-filtered by the chart beside them.",
             "- Add the synthetic-data footer text box to every page.", "- Give each page a tooltip-free, readable title; check the colour of a region is the same on every page.", "",
             "## 7. Save", "",
             "Save as `powerbi/retail_bi_dashboard.pbix`. Export one PNG per page to `docs/images/` (File > Export or screenshot). "
             "The `.pbix` can be committed if it is under GitHub's 100 MB limit.", "",
             "## 8. Validation checklist (the part that makes the dashboard trustworthy)", ""]
    if clean_df is None:
        lines += ["_Run `python run_pipeline.py` to fill in the numbers._", ""]
    else:
        cases, monthly, anomaly = validation_cases(clean_df, detections)
        lines += ["The numbers below come from the SQL/Python pipeline, not from Power BI. Set the slicers as described, "
                  "then compare each card. Differences larger than rounding mean a measure, relationship or slicer is wrong.", ""]
        headers = ["Slicer setting", "Revenue", "Profit", "Margin %", "Distinct orders", "AOV", "Active customers", "Repeat rate", "Top-10 share", "Pass?"]
        rows = []
        for label, k in cases:
            rows.append((label, f"{k['revenue']:,.2f}", f"{k['profit']:,.2f}", f"{k['profit_margin_pct']:.2f}%",
                         f"{k['distinct_orders']:,}", f"{k['avg_order_value']:,.2f}", f"{k['active_customers']:,}",
                         f"{k['repeat_customer_rate'] * 100:.1f}%", f"{k['top10_share_pct']:.1f}%", "[ ]"))
        lines += ["### 8.1 Headline KPIs", "", _table(headers, rows), "",
                  "### 8.2 Month-on-month % (matrix with `dim_date[year_month]`, measures [Revenue MoM %] and [Profit MoM %])", ""]
        last = monthly.tail(4)
        lines += [_table(["Month", "Revenue", "Revenue MoM %", "Profit MoM %", "Pass?"],
                         [(r["month"], f"{r['revenue']:,.2f}", _pct(r["revenue_change_pct"]), _pct(r["profit_change_pct"]), "[ ]")
                          for _, r in last.iterrows()]), "",
                  "The full list is `data/exports/kpi_monthly.csv`.", "",
                  "### 8.3 Anomaly Monitor (no slicers except the defaults)", "",
                  _table(["Measure", "Expected", "Pass?"],
                         [("[Flagged Weeks]", f"{anomaly['flags']:,}", "[ ]"), ("[Scored Weeks]", f"{anomaly['scored']:,}", "[ ]"),
                          ("[Extreme Flags]", f"{anomaly['extreme']:,}", "[ ]"), ("[Max Abs Deviation]", f"{anomaly['max_dev']:.1f}", "[ ]")]), ""]
    lines += ["### 8.4 Behaviour checks", "",
              "- [ ] Selecting Region = South changes every card on pages 1-4 and does not break any visual.",
              "- [ ] Clearing all slicers returns the 'No slicers' row above.",
              "- [ ] A date range that contains no data shows blanks/zeros, not errors.",
              "- [ ] Category matrix shows no Total for Distinct Orders.",
              "- [ ] Anomaly line chart is blank until one scope_value and one metric are selected, then shows the band and red flag markers.",
              "- [ ] Every page carries the synthetic-data footer.", "",
              "## 9. Troubleshooting", "",
              _table(["Symptom", "Likely cause", "Fix"],
                     [("Revenue is right but orders are too high", "Used COUNTROWS instead of DISTINCTCOUNT", "Use the [Distinct Orders] measure"),
                      ("MoM % blank everywhere", "dim_date not marked as date table, or relationship missing", "Model view, section 3"),
                      ("Region slicer does nothing", "Relationship dim_customer -> fact_sales missing or reversed", "Check direction is Single, one side = dim_customer"),
                      ("Anomaly chart empty", "Slicers not single-select", "Select one scope_value and one metric"),
                      ("is_anomaly measures return 0 flags", "Column loaded as text", "Change type to True/False in Power Query"),
                      ("Totals do not add up in a category matrix", "Distinct counts are not additive", "Hide the Total row for that measure")]), ""]
    return "\n".join(lines)


def _pct(value):
    return "n/a" if pd.isna(value) else f"{value:+.2f}%"


# ============================================================================= WRITING
def render_all(clean_df=None, detections=None):
    return {"dashboard_spec.md": render_dashboard_spec(), "data_model.md": render_data_model(),
            "dax_measures.md": render_dax_measures(), "build_guide.md": render_build_guide(clean_df, detections)}


def write_all(clean_df=None, detections=None, directory=POWERBI_DIR):
    directory.mkdir(parents=True, exist_ok=True)
    documents = render_all(clean_df, detections)
    for name, text in documents.items():
        (directory / name).write_text(text, encoding="utf-8")
    return sorted(documents)


def main():
    from src import anomaly_detection, database
    clean = database.load_clean_sales()
    names = write_all(clean, anomaly_detection.detect(clean))
    print(f"Power BI specification written to {POWERBI_DIR} ({', '.join(names)})")


if __name__ == "__main__":
    main()
