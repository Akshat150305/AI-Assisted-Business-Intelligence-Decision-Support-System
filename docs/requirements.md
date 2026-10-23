# Requirements Document: AI-Assisted Business Intelligence & Decision Support System

**Version:** 1.0 (Phase 1)
**Status:** Draft for review
**Data note:** All data in this project is **synthetic**, generated with a fixed random seed. Nothing in this project represents the results of a real company.

---

## 1. Business problem and objectives

### 1.1 Problem

A retail business generates sales and customer data every day, but decision-makers often struggle to:

- Notice unusual changes in revenue and profitability early.
- See which products, customers, categories, and regions are performing well or poorly.
- Trust the numbers, because the same KPI is calculated differently in different places.
- Avoid repetitive manual reporting.
- Turn raw numbers into clear findings and next steps.

### 1.2 Objectives

| ID | Objective |
|---|---|
| OBJ-1 | Produce a reproducible, validated synthetic retail dataset with documented data-quality handling. |
| OBJ-2 | Calculate reliable, consistently defined KPIs in SQL and Python that reconcile with each other. |
| OBJ-3 | Flag statistically unusual revenue and profit movements using an explainable method that uses only past data. |
| OBJ-4 | Provide an interactive Power BI dashboard (5 pages) that answers stakeholders' practical questions. |
| OBJ-5 | Generate evidence-backed findings and recommended actions from measured values, not hardcoded text. |
| OBJ-6 | Automate the refresh of data, KPIs, findings, and exports with one documented command. |

### 1.3 Questions the system must help answer

1. Is revenue increasing or decreasing?
2. Which products contribute the most profit?
3. Which regions have declining sales or weak margins?
4. Which customers contribute disproportionately to revenue?
5. Are there unusual changes that likely need investigation?
6. What action should a stakeholder consider next?

---

## 2. Stakeholders and information needs

| Stakeholder | Main information needs | Typical questions | Primary dashboard page |
|---|---|---|---|
| Business manager | Overall health of the business, alerts, headline trends | Is revenue growing? Is profit margin holding? What needs attention? | Executive Overview |
| Sales manager | Sales trends, customer concentration, buying behaviour | Which customers and segments drive revenue? Are repeat purchases occurring? | Customer Analysis |
| Product/category manager | Product and category contribution and margin | Which products earn the most profit? Which have weak margin? | Product & Category Performance |
| Regional manager | Regional sales, margin, and trend | Is my region declining? How does it compare with others? | Regional Performance |
| Business analyst | Data quality, KPI definitions, anomalies, traceable evidence | Can I trust these numbers? Which anomalies should I investigate first? | Anomaly Monitor, plus the data-quality summary |

---

## 3. Functional requirements

### 3.1 Data generation and quality

| ID | Requirement |
|---|---|
| FR-01 | Generate a synthetic retail dataset with a fixed random seed so that the same seed always produces identical data. |
| FR-02 | The dataset contains these fields: order ID, line number, order date, customer ID, customer segment, product ID, product name, category, region, quantity, unit price, revenue, cost, profit. |
| FR-03 | The dataset spans enough dates and records to support monthly trends, weekly anomaly detection, and segment comparisons. |
| FR-04 | Include realistic variation in demand by category, performance by region, and purchasing patterns by segment. |
| FR-05 | Inject a small number of documented anomalies. Their labels are stored in a separate file and are never used as input to the detection algorithm. |
| FR-06 | Inject a small number of documented data-quality defects (for example missing values, duplicates, invalid values) into the raw data so that validation can be tested. |
| FR-07 | Validate: missing values, duplicate records and order identifiers, invalid dates, invalid quantities and prices, revenue/cost/profit consistency, and unexpected category and region values. |
| FR-08 | Record every cleaning decision in an audit log (record identifier, rule, action, reason). No record is removed or changed without a logged reason. |
| FR-09 | Produce a data dictionary and a short data-quality summary. |

### 3.2 Storage, SQL, and KPIs

| ID | Requirement |
|---|---|
| FR-10 | Load the cleaned data into a SQLite database. |
| FR-11 | Provide SQL queries for: revenue and profit by month, by region, by product and category; customer-level metrics; order counts and average order value; profit margin; period-over-period comparisons. |
| FR-12 | Calculate the KPIs defined in Section 8 in Python, using the same formulas as SQL. |
| FR-13 | Distinguish orders (distinct order ID) from order lines (rows). |
| FR-14 | Handle zero denominators and empty data without errors (return no value instead of dividing by zero). |
| FR-15 | Support filtering by date range, region, category, and customer segment, with the same figures in SQL and Python. |

### 3.3 Anomaly detection

| ID | Requirement |
|---|---|
| FR-16 | Detect unusually high and low revenue and profit using a rolling baseline built only from earlier periods. |
| FR-17 | Each flag reports: observed value, baseline, deviation, threshold, direction (high/low), and severity. |
| FR-18 | Handle insufficient history and zero variance without false flags or crashes. |
| FR-19 | Be testable against the injected synthetic anomalies, using labels only for evaluation after detection. |
| FR-20 | Describe results as "statistical anomalies requiring investigation", never as confirmed business issues. |

### 3.4 Insights and recommendations

| ID | Requirement |
|---|---|
| FR-21 | A rule-based insight engine generates findings for material revenue and profit changes, strong and weak products/categories, and regional differences. |
| FR-22 | Every finding contains: finding ID, metric and period, observed value, comparison baseline, absolute and percentage change (where meaningful), supporting evidence, business interpretation, recommended action, and confidence/limitations. |
| FR-23 | Each finding separates: observed fact, possible explanation, recommended action, and evidence needed to validate the explanation. |
| FR-24 | Findings are ranked by magnitude and business relevance. |
| FR-25 | Findings use values calculated by the pipeline. No hardcoded results. |
| FR-26 | (Optional, after the core works) An LLM layer rewrites validated findings as plain-language summaries, with a deterministic fallback. The core system never depends on it. |

### 3.5 Power BI and reporting

| ID | Requirement |
|---|---|
| FR-27 | Export documented, Power BI-ready CSV tables. |
| FR-28 | Provide a dashboard specification, DAX measures, field mappings, and step-by-step build instructions. |
| FR-29 | The dashboard has 5 pages: Executive Overview, Product & Category Performance, Customer Analysis, Regional Performance, Anomaly Monitor. |
| FR-30 | The dashboard has date, region, category, and segment slicers where relevant. |
| FR-31 | Provide a single refresh script that regenerates all data, calculations, findings, and exports. |
| FR-32 | Produce a concise business findings report and a short stakeholder presentation using actual calculated results. |
| FR-33 | Provide an exportable analytical summary (CSV or Markdown). |

### 3.6 Testing

| ID | Requirement |
|---|---|
| FR-34 | Automated pytest tests cover validation, KPI calculations, SQL/Python agreement, distinct order counting, margins, filtering, anomaly detection, empty data, zero denominators, recommendation rules, and export consistency. |
| FR-35 | Tests confirm that anomaly labels are not used by the detection algorithm. |

---

## 4. Non-functional requirements

| ID | Requirement | How it is checked |
|---|---|---|
| NFR-01 | **Reproducibility:** the whole project can be recreated from documented commands and a fixed seed. | Run the refresh script twice; outputs are identical. |
| NFR-02 | **Correctness:** KPI values are identical across SQL, Python, exports, and Power BI for the same filters. | Reconciliation tests; DAX check against exported KPI summary. |
| NFR-03 | **Transparency:** every calculation and rule is documented and explainable by the student. | KPI catalogue, code comments, README. |
| NFR-04 | **Auditability:** cleaning decisions and findings are traceable to source records and calculations. | Cleaning audit log; finding evidence fields. |
| NFR-05 | **Local-only:** runs on a normal Windows laptop with Python, SQLite, and Power BI Desktop. No cloud service is required. | Fresh-machine setup test. |
| NFR-06 | **Performance:** full refresh completes in well under a few minutes at the planned data size (tens of thousands of order lines). | Timed refresh run. |
| NFR-07 | **Modularity:** each pipeline stage is a separate, testable module with a clear input and output. | Repository structure; unit tests. |
| NFR-08 | **Security:** no API keys, secrets, or private data in the repository. | `.gitignore`; review before commit. |
| NFR-09 | **Honesty:** synthetic data is labelled everywhere. No unmeasured business-impact claims. Dashboard completion is only claimed once actually built and tested. | Review of README, report, and slides. |
| NFR-10 | **Robustness:** empty inputs, missing values, and zero denominators never crash the pipeline. | Edge-case tests. |

---

## 5. Scope and exclusions

### 5.1 In scope

- Synthetic retail data, validation, cleaning, SQLite storage, SQL analysis.
- KPI calculations and rolling-baseline anomaly detection.
- Rule-based insight engine.
- Power BI-ready exports, DAX measures, and a build guide for 5 dashboard pages.
- Findings report, presentation, README, and automated tests.

### 5.2 Out of scope

| Exclusion | Reason |
|---|---|
| Real company data | The project uses synthetic data only. |
| Forecasting or prediction | Would require a separately evaluated model. No predictive claims will be made. |
| Customer churn or "at-risk" labels | Needs a defensible definition and enough data history. We report repeat-purchase indicators only. |
| Streamlit as the main interface | Power BI is the primary dashboard. A Python preview is allowed only as a clearly labelled fallback. |
| Mandatory LLM or external API use | Optional extra, built only after the core works. |
| Live data connections, scheduling, user logins, row-level security | Not needed for a local MVP. |
| Inventory, returns, discounts, taxes, shipping, multiple currencies | Kept out to keep metric definitions simple and reliable. |
| Causal conclusions | Statistical flags are not proof of cause. |

---

## 6. User workflows

| # | Workflow | Steps |
|---|---|---|
| W1 | **Refresh the system** (analyst) | Run the refresh script, then data is generated, validated, cleaned, loaded, analysed, and exported. Then open or refresh the Power BI file. |
| W2 | **Review business health** (business manager) | Open Executive Overview, check KPI cards and period-over-period changes, then read the key findings and alerts. |
| W3 | **Investigate a product or region** (product/regional manager) | Filter by category or region, compare margin and trend, then read the related finding and recommended action. |
| W4 | **Review customers** (sales manager) | Open Customer Analysis, check top customers, concentration, and repeat-purchase rate by segment. |
| W5 | **Investigate an anomaly** (business analyst) | Open Anomaly Monitor, sort by severity, compare actual with baseline, check the supporting context and "evidence needed", then decide whether to investigate. |
| W6 | **Check data quality** (business analyst) | Read the data-quality summary and cleaning audit log to see what was fixed or removed and why. |

---

## 7. Data assumptions and limitations

### 7.1 Assumptions

1. **Grain:** one row is one **order line** (one product within one order). An order can contain several lines. The business key of a line is `(order_id, line_number)`. We add `line_number` to the required field list so that duplicate detection is well-defined.
2. **Orders:** an order has one customer, one date, and one region. A distinct `order_id` counts as one order.
3. **Customers:** each customer has one segment and one region for the whole dataset. A customer's orders all belong to that region.
4. **Products:** each product has one category and a fixed base unit price and unit cost, which may vary slightly over time.
5. **Money:** a single generic currency. Revenue = quantity × unit price. Cost = quantity × unit cost (stored as a line total). Profit = revenue − cost. No discounts, taxes, or returns.
6. **Dates:** daily order dates over a fixed multi-month range, with seasonality and a mild trend.
7. **Injected events:** a small number of anomalies and data defects are added deliberately and documented in a separate labels file.

### 7.2 Limitations

- Synthetic data cannot show real customer behaviour. Patterns come from generation rules.
- Findings describe the simulated data only and must never be presented as real company results.
- Statistical anomalies are signals to investigate, not confirmed problems.
- Repeat-purchase measures depend on the generated purchase frequency. No churn definition is claimed.
- Possible explanations in findings are hypotheses. They need extra evidence before being accepted.

### 7.3 Data ownership (to avoid inconsistent calculations)

| Layer | Owns | Notes |
|---|---|---|
| Python (`metrics.py`) | KPI formulas, anomaly detection, findings | Source of truth for business logic. |
| SQL (`sql/`) | Aggregation queries over the cleaned database | Must reconcile with Python in tests. |
| Exports (`data/exports/`) | Cleaned fact table, dimension tables, pre-computed anomaly and finding tables, KPI summary | The only inputs to Power BI. |
| DAX | Standard KPIs (revenue, profit, margin, orders, AOV, period-over-period) calculated on the exported fact table | Verified against the exported KPI summary. DAX does not recompute anomalies or findings. |

---

## 8. KPI definitions

All KPIs use cleaned data. Any ratio returns "no value" when its denominator is zero.

| KPI | Formula |
|---|---|
| Revenue | Sum of line revenue |
| Cost | Sum of line cost |
| Profit | Sum of (line revenue − line cost) |
| Profit margin % | Profit ÷ Revenue × 100 |
| Distinct orders | Count of distinct `order_id` |
| Order lines | Count of rows |
| Units sold | Sum of quantity |
| Average order value (AOV) | Revenue ÷ Distinct orders |
| Active customers | Count of distinct `customer_id` with at least one order in the period |
| Revenue per customer | Revenue ÷ Active customers |
| Repeat-customer rate | Customers with 2 or more distinct orders in the period ÷ Active customers |
| Revenue contribution % | Revenue of an item (product, category, region, customer) ÷ total Revenue of the same filtered selection |
| Top-N customer share | Revenue of the top N customers ÷ total Revenue |
| Period-over-period change | Current period value − previous period value; % change = (current − previous) ÷ previous × 100 |
| Anomaly deviation | (Observed − rolling baseline) ÷ rolling spread, using only earlier periods. The exact method and threshold are defined and documented in Phase 4. |

**Period-over-period rule:** the previous period is the immediately preceding period of the same length and grain (for example, this month vs last month). If no previous period exists, no change is shown.

---

## 9. Acceptance criteria

| ID | Criterion |
|---|---|
| AC-01 | Running data generation twice with the same seed produces identical raw files. |
| AC-02 | Validation detects every injected data-quality defect and writes an audit-log entry for each action taken. |
| AC-03 | No record is changed or removed without a logged reason. |
| AC-04 | Revenue, profit, margin, distinct orders, and AOV from SQL and Python match for the same filters. |
| AC-05 | Distinct order counts are never inflated by order lines. |
| AC-06 | Empty data and zero denominators return safe results and never crash. |
| AC-07 | Anomaly detection uses only earlier periods, and a test proves that changing future data does not change past flags. |
| AC-08 | Detection reports how many injected anomalies it found and how many false flags occurred, as measured results. |
| AC-09 | Every finding contains all fields listed in FR-22 and its numbers come from pipeline output. |
| AC-10 | Exported CSV totals match database totals. |
| AC-11 | The Power BI dashboard has the 5 pages and slicers specified, and its KPI cards match the exported KPI summary. |
| AC-12 | The refresh script rebuilds everything from documented commands. |
| AC-13 | All pytest tests pass when executed, and results are reported only after actually running them. |
| AC-14 | README, report, and presentation label the data as synthetic and contain no unmeasured impact claims. |

---

## 10. Requirements-to-feature traceability

| Requirement(s) | Feature / module | Delivered in | Verified by |
|---|---|---|---|
| OBJ-1, FR-01 to FR-06 | `data_generator.py`, `data/raw/` | Phase 2 | AC-01; seed test |
| FR-07 to FR-09 | `validation.py`, `data_pipeline.py`, cleaning audit log, data dictionary | Phase 2 | AC-02, AC-03; validation tests |
| FR-10, FR-11 | `database.py`, `queries.py`, `sql/` | Phase 3 | AC-04; SQL tests |
| OBJ-2, FR-12 to FR-15 | `metrics.py`, KPI catalogue | Phase 3 and 4 | AC-04, AC-05, AC-06 |
| OBJ-3, FR-16 to FR-20 | `anomaly_detection.py` | Phase 4 | AC-07, AC-08 |
| OBJ-5, FR-21 to FR-25 | `recommendations.py` | Phase 6 | AC-09; rule tests |
| FR-26 | Optional LLM summary layer with fallback | Phase 6 (optional) | Fallback test |
| OBJ-4, FR-27 to FR-30 | `powerbi/` spec, DAX measures, exported CSVs, dashboard | Phase 3 (CSVs), Phase 5 (spec, build) | AC-10, AC-11 |
| OBJ-6, FR-31 | `run_pipeline.py` | Phase 3 onwards | AC-12 |
| FR-32, FR-33 | `reporting.py`, `reports/`, `presentation/` | Phase 7 | AC-14 |
| FR-34, FR-35, NFR-01 to NFR-10 | `tests/`, README | Phase 8 | AC-13 |

---

## 11. Open points for later phases

- Final volume of synthetic data (planned: roughly 18 to 24 months of daily orders).
- Exact anomaly method, window length, and threshold (Phase 4).
- Final thresholds for what counts as a "material" change in findings (Phase 6).
- Whether generated CSV and database files are committed to Git or regenerated (decided in Phase 3).
