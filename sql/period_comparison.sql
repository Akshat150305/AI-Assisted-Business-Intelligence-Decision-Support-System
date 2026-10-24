-- Aggregates for a CURRENT period and a PREVIOUS period, side by side (one row).
-- Parameters: :cur_start :cur_end :prev_start :prev_end (all inclusive, required)
--             :region :category :segment (NULL = no restriction)
--
-- This query only produces the period aggregates. The change columns (absolute and %) are
-- calculated by the shared helper pct_change() in src/metrics.py, so SQL and Python use one rule:
--     % change = (current - previous) / ABS(previous) * 100, and NULL when previous is 0 or missing.
-- Margin and AOV are derived in src/queries.py from these aggregates with safe_divide(), so a period
-- with no rows gives revenue 0 but a NULL margin and AOV (zero-denominator guard).
WITH both_periods AS (
    SELECT
        CASE WHEN order_date BETWEEN :cur_start  AND :cur_end  THEN 'cur'
             WHEN order_date BETWEEN :prev_start AND :prev_end THEN 'prev' END AS period,
        *
    FROM sales_lines
    WHERE (order_date BETWEEN :cur_start AND :cur_end OR order_date BETWEEN :prev_start AND :prev_end)
      AND (:region   IS NULL OR region = :region)
      AND (:category IS NULL OR category = :category)
      AND (:segment  IS NULL OR customer_segment = :segment)
)
SELECT
    COALESCE(SUM(CASE WHEN period = 'cur'  THEN revenue END), 0)           AS cur_revenue,
    COALESCE(SUM(CASE WHEN period = 'prev' THEN revenue END), 0)           AS prev_revenue,
    COALESCE(SUM(CASE WHEN period = 'cur'  THEN cost END), 0)              AS cur_cost,
    COALESCE(SUM(CASE WHEN period = 'prev' THEN cost END), 0)              AS prev_cost,
    COALESCE(SUM(CASE WHEN period = 'cur'  THEN profit END), 0)            AS cur_profit,
    COALESCE(SUM(CASE WHEN period = 'prev' THEN profit END), 0)            AS prev_profit,
    COUNT(DISTINCT CASE WHEN period = 'cur'  THEN order_id END)            AS cur_distinct_orders,
    COUNT(DISTINCT CASE WHEN period = 'prev' THEN order_id END)            AS prev_distinct_orders,
    SUM(CASE WHEN period = 'cur'  THEN 1 ELSE 0 END)                       AS cur_order_lines,
    SUM(CASE WHEN period = 'prev' THEN 1 ELSE 0 END)                       AS prev_order_lines,
    COALESCE(SUM(CASE WHEN period = 'cur'  THEN quantity END), 0)          AS cur_units_sold,
    COALESCE(SUM(CASE WHEN period = 'prev' THEN quantity END), 0)          AS prev_units_sold,
    COUNT(DISTINCT CASE WHEN period = 'cur'  THEN customer_id END)         AS cur_active_customers,
    COUNT(DISTINCT CASE WHEN period = 'prev' THEN customer_id END)         AS prev_active_customers
FROM both_periods;
