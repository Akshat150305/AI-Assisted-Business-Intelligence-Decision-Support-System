# Data Dictionary (Phase 2 files)

> **All data is synthetic.** It is produced by `src/data_generator.py` from a fixed random seed (`SEED = 42` in `app_config/config.py`). It does not represent any real company. Prices and money are in one generic currency.

**Grain:** one row = one **order line** (one product inside one order). The business key is `(order_id, line_number)`. An order can have several lines, so *count distinct `order_id`* to count orders, never count rows.

---

## 1. `data/raw/sales_raw.csv` and `data/processed/sales_clean.csv`

Both files have the same 14 columns. The raw file contains planted defects. The clean file has passed every rule in `src/validation.py`.

| Column | Type | Description | Rule in the clean file |
|---|---|---|---|
| `order_id` | text | Order identifier, for example `ORD-000123`. | Not blank. All lines of one order share customer, date and region. |
| `line_number` | integer | Position of the line inside the order, starting at 1. | Not blank. `(order_id, line_number)` is unique. |
| `order_date` | date `YYYY-MM-DD` | Date the order was placed. | Valid date between `START_DATE` and `END_DATE` in the config. |
| `customer_id` | text | Customer identifier, for example `C0042`. | Not blank. |
| `customer_segment` | text | `Consumer`, `Small Business` or `Corporate`. | One of the allowed values. One segment per customer. |
| `product_id` | text | Product identifier, for example `P007`. | Not blank. |
| `product_name` | text | Product name. | Not blank. One name per product. |
| `category` | text | One of the 5 categories in `config.CATEGORIES`. | One of the allowed values. One category per product. |
| `region` | text | `North`, `South`, `East` or `West`. | One of the allowed values. One region per customer. |
| `quantity` | integer | Units sold on the line. | Whole number greater than 0. |
| `unit_price` | decimal | Selling price per unit. | Greater than 0 and within 0.5x to 2x of the product's median price. |
| `revenue` | decimal | `quantity x unit_price`. | Equals the formula within 0.01. |
| `cost` | decimal | Total cost of the line (not per unit). | Not negative. |
| `profit` | decimal | `revenue - cost`. | Equals the formula within 0.01. |

Product reference data (40 products, 5 categories) is defined in `PRODUCTS` in `src/data_generator.py`. The raw file is read as text so that bad values such as `N/A` can be reported instead of silently converted.

---

## 2. `data/processed/cleaning_audit_log.csv`

One row per cleaning action. Nothing is changed or removed without an entry here.

| Column | Description |
|---|---|
| `log_id` | Unique id, `L000001` onwards. |
| `source_row` | 1-based row number in `sales_raw.csv` (header excluded). |
| `order_id`, `line_number` | Key of the affected raw row, as it appeared in the raw file (can be blank). |
| `rule_id`, `rule_name` | Rule that fired. See the rule table below. |
| `action` | `REPAIRED` (value changed) or `REMOVED` (row quarantined). |
| `column` | Column that was repaired or that caused the removal. |
| `old_value` | Value in the raw file. |
| `new_value` | Value written to the clean file (blank for removals). |
| `reason` | Plain-language explanation of the decision. |

A removed row can have several entries if it broke several rules.

## 3. `data/processed/rejected_rows.csv`

The quarantined rows exactly as they appeared in the raw file, plus `source_row`, `rejection_rules` and `rejection_reasons`. They are kept so that a data owner can review them. `raw rows = clean rows + rejected rows` always holds.

## 4. Rule catalogue

Defined in `RULES` in `src/validation.py` (the single source of truth). The generated `reports/data_quality_summary.md` shows the rules with the number of rows each one affected in the latest run.

| Rule | Check | Action |
|---|---|---|
| R01 | Exact duplicate row | Remove later copies |
| R02 | Same `(order_id, line_number)` with different content | Keep first, remove others |
| R03 | Required field blank and not rebuildable | Remove |
| R04 | Invalid date or outside the allowed period | Remove |
| R05 | Quantity not a positive whole number | Remove |
| R06 | Unit price zero, negative or not a number | Remove |
| R07 | Revenue differs from `quantity x unit_price` | Recalculate |
| R08 | Unit price far outside the product's normal range | Remove |
| R09 | Lines of one order disagree on customer, date or region | Keep the majority, remove the rest |
| R10 | Profit differs from `revenue - cost` | Recalculate |
| R11 | Extra spaces or different upper/lower case | Standardise |
| R12 | Category, region or segment not an allowed value | Replace from other rows of the same product/customer |
| R13 | Blank value that can be rebuilt | Rebuild from other rows (or `unit_price = revenue / quantity`) |
| R14 | Cost negative or not a number | Remove |

---

## 5. Label files (for testing only, never used as algorithm input)

### `data/raw/injected_anomalies.csv`: planted business anomalies

Used in Phase 4 to **evaluate** the anomaly detector after it has run. Not read by cleaning, KPIs or detection.

| Column | Description |
|---|---|
| `anomaly_id` | `A1` to `A4`. |
| `anomaly_type` | `revenue_spike`, `revenue_drop`, `margin_squeeze` or `single_large_order`. |
| `start_date`, `end_date` | Window in which the simulated event applies. |
| `region`, `category` | Scope of the event (blank = all). |
| `affected_metric` | `revenue` or `profit_margin`. |
| `multiplier` | Strength of the simulated change. |
| `affected_order_id` | Only for the single large order. |
| `description` | What was simulated. These are simulated events, not real causes. |

### `data/raw/injected_defects.csv`: planted data-quality defects

Used only by `evaluate_against_defect_labels` and the tests, after cleaning, to measure whether each defect was handled as expected. The cleaning code does not read it.

| Column | Description |
|---|---|
| `defect_id` | `D001` onwards. |
| `source_row`, `order_id`, `line_number` | Raw row that was damaged. |
| `defect_type` | What was planted, for example `negative_quantity`. |
| `description` | Exact change made. |
| `expected_action`, `expected_rule` | What a correct pipeline should do. |

---

## 6. Modelled patterns in the synthetic data

These are rules in the generator, listed so nobody mistakes them for real-world findings:

- Daily order volume has weekday and month seasonality and a small overall growth trend.
- Regions differ in volume growth: West and South grow, East declines, North is roughly flat.
- Categories differ in margin (for example Electronics is lower margin than Beauty & Personal Care) and in seasonal demand.
- A minority of customers place many more orders than the rest, so revenue is concentrated.
- Four business anomalies are planted (see 5).

---

## 7. SQLite database (`data/processed/retail_bi.db`, Phase 3)

Rebuilt from `sales_clean.csv` by `python -m src.database` (git-ignored). Schema: `sql/01_schema.sql`.

| Table | Grain | Purpose |
|---|---|---|
| `sales_lines` | one order line, key `(order_id, line_number)` | The fact table. All SQL queries read it. It is "wide" (region, category ... on every row) to keep queries simple. |
| `dim_product` | one product | `product_id`, `product_name`, `category`. Guarantees one category per product. |
| `dim_customer` | one customer | `customer_id`, `customer_segment`, `region`. Guarantees one segment and one region per customer. |

The database enforces: primary key (no duplicate order line), foreign keys to both dimensions, `quantity > 0`, `unit_price > 0`, `cost >= 0` and an ISO `YYYY-MM-DD` date format.

### SQL files (`sql/`)

Every query accepts the same optional filters (`NULL` = no restriction): `:start_date`, `:end_date` (inclusive), `:region`, `:category`, `:segment`. `period_comparison.sql` takes the two periods instead of start/end dates.

| File | Returns |
|---|---|
| `kpi_summary.sql` | One row of headline KPIs |
| `monthly_trend.sql` | KPIs by month with month-over-month change |
| `by_region.sql`, `by_category.sql`, `by_segment.sql`, `by_product.sql` | KPIs and revenue/profit share by that dimension |
| `region_monthly.sql` | KPIs by region and month |
| `customer_metrics.sql` | One row per customer, with repeat-customer flag |
| `period_comparison.sql` | Current vs previous period aggregates |

## 8. Power BI export tables (`data/exports/`, Phase 3)

Written by `python -m src.powerbi_export` (and by `run_pipeline.py`). Star schema: `fact_sales` joined to three dimensions. `fact_sales.csv` is git-ignored (about 3 MB, regenerable); the other tables are small and committed.

| File | Grain | Columns |
|---|---|---|
| `fact_sales.csv` | order line | `order_id, line_number, order_date, customer_id, product_id, quantity, unit_price, revenue, cost, profit` |
| `dim_date.csv` | calendar day (complete, no gaps) | `date, year, quarter, month_number, month_name, year_month, month_start, week_start (Monday), day_of_week_number, day_name, is_weekend` |
| `dim_product.csv` | product | `product_id, product_name, category` |
| `dim_customer.csv` | customer | `customer_id, customer_segment, region` |
| `kpi_summary.csv` | 1 row | All headline KPIs from SQL. **Answer key for checking the DAX measures.** |
| `kpi_monthly.csv` | month | Monthly KPIs from SQL. Answer key by month. |
| `customer_metrics.csv` | customer | Purchasing metrics from SQL (customer analysis page). |

Relationships to create in Power BI: `fact_sales[order_date]` to `dim_date[date]`, `fact_sales[product_id]` to `dim_product[product_id]`, `fact_sales[customer_id]` to `dim_customer[customer_id]` (all many-to-one, single direction from dimension to fact). Region and segment are customer attributes, category is a product attribute.

### Caution when adding up numbers

**Distinct counts are not additive.** An order has one region and one segment, so order counts by region add up to the total. An order can contain several categories, so order counts by category add up to MORE than the total. The tests check both facts. Revenue and profit are additive across every dimension.

---

## 9. Anomaly table for Power BI (`data/exports/anomaly_weekly.csv`, Phase 4)

Written by `run_pipeline.py` (step 5). One row per **series x complete week**, not only the flagged ones, so the Anomaly Monitor page can draw actual vs expected for every week. Filter `is_anomaly = True` for the flags. Method, parameters and results: `reports/anomaly_evaluation.md`.

A series is one metric (`revenue`, `profit` or `profit_margin_pct`) for one scope: the whole business (`scope_type = all`), a region, a category or a customer segment. Weeks run Monday to Sunday and only complete weeks are scored.

| Column | Description |
|---|---|
| `scope_type`, `scope_value` | `all`/`All`, `region`/`North`..., `category`/`Electronics`..., `customer_segment`/`Corporate`... |
| `metric` | `revenue`, `profit` or `profit_margin_pct` |
| `week_start`, `week_end` | Monday and Sunday of the week |
| `observed` | The week's actual value |
| `baseline` | Median of the previous 13 complete weeks (earlier weeks only) |
| `spread` | 1.4826 x median absolute deviation of those weeks |
| `expected_low`, `expected_high` | `baseline -/+ threshold x spread`, the "normal" band |
| `deviation` | `(observed - baseline) / spread` (empty when not scored) |
| `threshold` | Flag when `abs(deviation)` reaches this value (4.5) |
| `status` | `evaluated`, `insufficient_history`, `zero_variance` or `no_observation` |
| `is_anomaly` | True when evaluated and `abs(deviation) >= threshold` |
| `direction` | `high` or `low` (flags only) |
| `severity` | `moderate`, `high` or `extreme` (flags only) |
| `suggested_check` | What an analyst should look at first (flags only). A question to investigate, not a cause. |

**A flag is a statistical anomaly, not a confirmed business issue, and not a forecast.** The planted-anomaly labels are never in this table.

### Other Phase 4 outputs

| File | Purpose |
|---|---|
| `docs/kpi_catalogue.md` | KPI catalogue, generated from `src/kpi_catalogue.py` (meaning, formula, source, grain, stakeholders, caveats) |
| `reports/anomaly_evaluation.md` | Generated evaluation: parameter history, planted events detected or not, false alarms, sensitivity, limitations |

## 10. Power BI specification (`powerbi/`, Phase 5)

Generated by `src/powerbi_spec.py` (run by `run_pipeline.py`, or `python -m src.powerbi_spec`). Do not edit by hand: a test fails if a file is out of date.

| File | Purpose |
|---|---|
| `powerbi/dashboard_spec.md` | The 5 pages: stakeholder, questions answered, slicers, every visual with its fields, global design rules, build-status table |
| `powerbi/data_model.md` | Tables, columns and Power BI data types, relationships, sort-by and hiding settings, field mapping |
| `powerbi/dax_measures.md` | Every DAX measure with format, explanation and the answer-key column that verifies it |
| `powerbi/build_guide.md` | Step-by-step build instructions, a validation checklist with numbers computed by the pipeline, troubleshooting |

Status: these files are a **specification**. The dashboard (`.pbix`) counts as built and validated only after the checklist in `build_guide.md` section 8 has been completed in Power BI Desktop.
