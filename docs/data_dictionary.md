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
