"""The KPI catalogue: one documented definition per KPI.

The catalogue is code, not just a document, so tests can check that
  * every KPI calculated in src/metrics.py is documented,
  * every SQL file and Python function it names really exists,
  * docs/kpi_catalogue.md is up to date (it is generated from this list).

Run from the project root to regenerate the document:   python -m src.kpi_catalogue
ALL DATA IS SYNTHETIC.
"""
from app_config import config

CATALOGUE_FILE = config.PROJECT_ROOT / "docs" / "kpi_catalogue.md"

STAKEHOLDERS = ["Business manager", "Sales manager", "Product/category manager",
                "Regional manager", "Business analyst"]
REQUIRED_FIELDS = ["name", "meaning", "formula", "data_source", "grain", "stakeholders", "caveats"]

ALL = STAKEHOLDERS
KPIS = [
    {
        "key": "revenue", "name": "Revenue",
        "meaning": "Total value of goods sold. The basic measure of business size.",
        "formula": "SUM(revenue) over order lines, where line revenue = quantity x unit_price",
        "data_source": "sales_lines (clean order lines)", "sql_file": "kpi_summary",
        "python_function": "src.metrics.compute_kpis",
        "grain": "Any slice: total, month, week, region, category, segment, product, customer",
        "stakeholders": ALL,
        "caveats": "Gross sales in one generic currency. No discounts, returns or taxes are modelled.",
    },
    {
        "key": "cost", "name": "Cost",
        "meaning": "Total cost of the goods sold.",
        "formula": "SUM(cost) over order lines (line cost is already the total for the line)",
        "data_source": "sales_lines", "sql_file": "kpi_summary",
        "python_function": "src.metrics.compute_kpis",
        "grain": "Any slice",
        "stakeholders": ["Business manager", "Product/category manager", "Business analyst"],
        "caveats": "Product cost only. Overheads, shipping and staff costs are not in the data.",
    },
    {
        "key": "profit", "name": "Profit",
        "meaning": "What is left after the cost of goods. Shows which activity actually earns money.",
        "formula": "SUM(profit), where line profit = revenue - cost (so profit = Revenue - Cost)",
        "data_source": "sales_lines", "sql_file": "kpi_summary",
        "python_function": "src.metrics.compute_kpis",
        "grain": "Any slice",
        "stakeholders": ALL,
        "caveats": "Gross profit, not net profit. A negative value means the slice sold below cost.",
    },
    {
        "key": "profit_margin_pct", "name": "Profit margin %",
        "meaning": "How many cents of profit each unit of revenue produces. Compares slices of different size fairly.",
        "formula": "Profit / Revenue x 100. Not calculated (empty) when Revenue is 0",
        "data_source": "sales_lines", "sql_file": "kpi_summary",
        "python_function": "src.metrics.compute_kpis",
        "grain": "Any slice",
        "stakeholders": ["Business manager", "Product/category manager", "Regional manager", "Business analyst"],
        "caveats": "Never average margins of slices. Recalculate it from summed profit and summed revenue, "
                   "otherwise small slices get too much weight.",
    },
    {
        "key": "distinct_orders", "name": "Distinct orders",
        "meaning": "Number of separate purchases (orders), regardless of how many products each contained.",
        "formula": "COUNT(DISTINCT order_id)",
        "data_source": "sales_lines", "sql_file": "kpi_summary",
        "python_function": "src.metrics.compute_kpis",
        "grain": "Any slice",
        "stakeholders": ["Business manager", "Sales manager", "Regional manager", "Business analyst"],
        "caveats": "Not additive across categories or products: one order can contain several. It is additive "
                   "across regions and segments because an order has exactly one of each.",
    },
    {
        "key": "order_lines", "name": "Order lines",
        "meaning": "Number of product lines sold (rows in the data). Used to see basket size, not to count orders.",
        "formula": "COUNT(*) over order lines",
        "data_source": "sales_lines", "sql_file": "kpi_summary",
        "python_function": "src.metrics.compute_kpis",
        "grain": "Any slice",
        "stakeholders": ["Business analyst", "Sales manager"],
        "caveats": "Never use this as the order count. It is larger than the number of orders.",
    },
    {
        "key": "units_sold", "name": "Units sold",
        "meaning": "Total quantity of items sold.",
        "formula": "SUM(quantity)",
        "data_source": "sales_lines", "sql_file": "kpi_summary",
        "python_function": "src.metrics.compute_kpis",
        "grain": "Any slice",
        "stakeholders": ["Product/category manager", "Regional manager"],
        "caveats": "Mixes cheap and expensive items, so units can rise while revenue falls.",
    },
    {
        "key": "avg_order_value", "name": "Average order value (AOV)",
        "meaning": "Typical size of one purchase.",
        "formula": "Revenue / Distinct orders. Not calculated when there are no orders",
        "data_source": "sales_lines", "sql_file": "kpi_summary",
        "python_function": "src.metrics.compute_kpis",
        "grain": "Any slice",
        "stakeholders": ["Business manager", "Sales manager", "Regional manager"],
        "caveats": "Divides by orders, not by order lines. Heavily influenced by a few very large orders "
                   "(Corporate customers), so compare it by segment.",
    },
    {
        "key": "active_customers", "name": "Active customers",
        "meaning": "How many different customers bought in the selected period.",
        "formula": "COUNT(DISTINCT customer_id)",
        "data_source": "sales_lines", "sql_file": "kpi_summary",
        "python_function": "src.metrics.compute_kpis",
        "grain": "Any slice",
        "stakeholders": ["Sales manager", "Business manager"],
        "caveats": "Depends on the period length. The count for a year is not the sum of the monthly counts.",
    },
    {
        "key": "revenue_per_customer", "name": "Revenue per customer",
        "meaning": "Average revenue earned from each active customer.",
        "formula": "Revenue / Active customers. Not calculated when there are no customers",
        "data_source": "sales_lines", "sql_file": "kpi_summary",
        "python_function": "src.metrics.compute_kpis",
        "grain": "Any slice",
        "stakeholders": ["Sales manager", "Business manager"],
        "caveats": "An average. A few large customers pull it up, so read it together with customer concentration.",
    },
    {
        "key": "repeat_customer_rate", "name": "Repeat-customer rate",
        "meaning": "Share of active customers who bought on more than one occasion in the period.",
        "formula": "Customers with 2 or more distinct orders / Active customers (a fraction from 0 to 1)",
        "data_source": "sales_lines", "sql_file": "kpi_summary",
        "python_function": "src.metrics.compute_kpis",
        "grain": "Any slice (customer_metrics.sql gives the per-customer flag)",
        "stakeholders": ["Sales manager", "Business analyst"],
        "caveats": "A repeat-purchase indicator only. It is NOT a churn or 'at risk' measure, which would need a "
                   "defensible definition. In the default synthetic dataset customers order very often over two years, "
                   "so the rate is close to 100% and tells little.",
    },
    {
        "key": None, "name": "Revenue / profit contribution %",
        "meaning": "Share of the total that one product, category, region, segment or customer provides.",
        "formula": "Revenue of the item / Total revenue of the same filtered selection x 100 "
                   "(same for profit)",
        "data_source": "sales_lines", "sql_file": "by_product",
        "python_function": "src.metrics.breakdown",
        "grain": "Product, category, region, customer segment (by_region, by_category, by_segment, by_product)",
        "stakeholders": ["Product/category manager", "Regional manager", "Business manager"],
        "caveats": "Shares always refer to the current filter. Change a slicer and every share changes.",
    },
    {
        "key": None, "name": "Top-N customer share",
        "meaning": "How concentrated revenue is: the share earned by the largest N customers (or top X% of customers).",
        "formula": "Revenue of the N biggest customers / Total revenue x 100",
        "data_source": "Per-customer revenue (customer_metrics)", "sql_file": "customer_metrics",
        "python_function": "src.metrics.top_n_customer_share",
        "grain": "Whole selection (computed from the customer table)",
        "stakeholders": ["Sales manager", "Business manager"],
        "caveats": "Describes concentration, not risk. Whether a high share is a problem depends on the business.",
    },
    {
        "key": None, "name": "Period-over-period change",
        "meaning": "How much a KPI moved compared with the previous period of the same length.",
        "formula": "Absolute change = current - previous. % change = (current - previous) / ABS(previous) x 100. "
                   "Not calculated when previous is 0 or missing",
        "data_source": "sales_lines", "sql_file": "period_comparison",
        "python_function": "src.metrics.compare_periods",
        "grain": "Any two non-overlapping periods; monthly_trend.sql gives month-over-month",
        "stakeholders": ALL,
        "caveats": "Ignores seasonality: December is expected to beat November. A short previous period "
                   "(for example one quiet week) can make the percentage look dramatic. Month-over-month "
                   "change is only shown when the previous calendar month exists.",
    },
    {
        "key": None, "name": "Anomaly deviation (robust z-score)",
        "meaning": "How unusual a week is compared with the previous weeks, measured in 'typical-variation units'.",
        "formula": f"(Observed - Baseline) / Spread, where Baseline = median of the previous "
                   f"{config.ANOMALY_WINDOW_WEEKS} complete weeks and Spread = 1.4826 x median absolute deviation "
                   f"of the same weeks. Flagged when |deviation| >= {config.ANOMALY_THRESHOLD:g}. "
                   f"Severity: moderate below {config.ANOMALY_SEVERITY_BANDS[0]:g}, high to "
                   f"{config.ANOMALY_SEVERITY_BANDS[1]:g}, extreme above",
        "data_source": "Weekly revenue, profit and margin by scope (derived from sales_lines)", "sql_file": None,
        "python_function": "src.anomaly_detection.detect",
        "grain": "Complete Monday-Sunday weeks, per series (total, region, category, segment)",
        "stakeholders": ["Business analyst", "Business manager", "Regional manager", "Product/category manager"],
        "caveats": "A STATISTICAL anomaly, not a confirmed business issue, and not a forecast. Uses earlier weeks "
                   "only. Not scored with fewer than "
                   f"{config.ANOMALY_MIN_HISTORY_WEEKS} earlier weeks or when earlier weeks are all identical. "
                   "Cannot anticipate seasonal peaks. Parameters and evaluation: reports/anomaly_evaluation.md.",
    },
]


def render_markdown():
    """The catalogue as Markdown, generated from KPIS."""
    lines = [
        "# KPI Catalogue",
        "",
        "> **Synthetic data.** All numbers in this project come from a generated dataset, not a real company.",
        "> This file is generated by `src/kpi_catalogue.py`. Edit the definitions there and re-run "
        "`python -m src.kpi_catalogue`.",
        "",
        "Every KPI uses the **cleaned** order lines (one row = one order line). A ratio is left empty, "
        "never 0 and never an error, when its denominator is 0. "
        "SQL, Python and Power BI (DAX) must give the same value for the same filters.",
        "",
        "## Overview",
        "",
        "| KPI | Formula | Main stakeholders |",
        "|---|---|---|",
    ]
    for kpi in KPIS:
        lines.append(f"| {kpi['name']} | {kpi['formula']} | {', '.join(kpi['stakeholders'])} |")
    lines += ["", "## Details", ""]
    for kpi in KPIS:
        sources = []
        if kpi["sql_file"]:
            sources.append(f"`sql/{kpi['sql_file']}.sql`")
        sources.append(f"`{kpi['python_function']}`")
        lines += [
            f"### {kpi['name']}",
            "",
            f"- **Business meaning:** {kpi['meaning']}",
            f"- **Formula:** {kpi['formula']}",
            f"- **Data source:** {kpi['data_source']}",
            f"- **Implemented in:** {', '.join(sources)}",
            f"- **Aggregation level:** {kpi['grain']}",
            f"- **Intended stakeholders:** {', '.join(kpi['stakeholders'])}",
            f"- **Interpretation caveats:** {kpi['caveats']}",
            "",
        ]
    lines += [
        "## Measure, calculated column or Power Query step?",
        "",
        "Used in Phase 5 when the KPIs are rebuilt in Power BI.",
        "",
        "| | Power Query transformation | Calculated column (DAX) | Measure (DAX) |",
        "|---|---|---|---|",
        "| When it runs | When data is loaded or refreshed | When data is loaded or refreshed | At query time, for the current filters |",
        "| Result stored? | Yes, in the table | Yes, one value per row | No, computed on demand |",
        "| Reacts to slicers? | No | No | **Yes** |",
        "| Typical use | Clean, reshape, join, change data types | Row-level label or flag (e.g. size band) | Revenue, Profit, Margin %, AOV, "
        "period-over-period change |",
        "",
        "All KPIs above are **measures**, because they must respond to slicers. Ratios are always measures that divide "
        "two summed measures (never an average of row-level ratios).",
    ]
    return "\n".join(lines) + "\n"


def write_catalogue(path=CATALOGUE_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(), encoding="utf-8")
    return path


def main():
    path = write_catalogue()
    print(f"KPI catalogue written to {path} ({len(KPIS)} KPIs)")


if __name__ == "__main__":
    main()
