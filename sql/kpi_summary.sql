-- KPI summary for the selected filters (one row).
-- Parameters (NULL = no restriction): :start_date :end_date :region :category :segment
--
-- Definitions (see docs/requirements.md, section 8):
--   revenue        = SUM(revenue)
--   profit         = SUM(profit)                        (profit per line = revenue - cost)
--   margin %       = profit / revenue * 100             (NULL when revenue is 0)
--   distinct_orders= COUNT(DISTINCT order_id)           (orders, NOT rows)
--   AOV            = revenue / distinct_orders          (NULL when there are no orders)
--   repeat rate    = customers with >= 2 distinct orders / active customers
WITH filtered AS (
    SELECT *
    FROM sales_lines
    WHERE (:start_date IS NULL OR order_date >= :start_date)
      AND (:end_date   IS NULL OR order_date <= :end_date)
      AND (:region     IS NULL OR region = :region)
      AND (:category   IS NULL OR category = :category)
      AND (:segment    IS NULL OR customer_segment = :segment)
),
repeat_customers AS (
    SELECT customer_id
    FROM filtered
    GROUP BY customer_id
    HAVING COUNT(DISTINCT order_id) >= 2
)
SELECT
    COALESCE(SUM(revenue), 0)                                   AS revenue,
    COALESCE(SUM(cost), 0)                                      AS cost,
    COALESCE(SUM(profit), 0)                                    AS profit,
    CASE WHEN COALESCE(SUM(revenue), 0) = 0 THEN NULL
         ELSE SUM(profit) * 100.0 / SUM(revenue) END            AS profit_margin_pct,
    COUNT(DISTINCT order_id)                                    AS distinct_orders,
    COUNT(*)                                                    AS order_lines,
    COALESCE(SUM(quantity), 0)                                  AS units_sold,
    CASE WHEN COUNT(DISTINCT order_id) = 0 THEN NULL
         ELSE SUM(revenue) / COUNT(DISTINCT order_id) END       AS avg_order_value,
    COUNT(DISTINCT customer_id)                                 AS active_customers,
    CASE WHEN COUNT(DISTINCT customer_id) = 0 THEN NULL
         ELSE SUM(revenue) / COUNT(DISTINCT customer_id) END    AS revenue_per_customer,
    CASE WHEN COUNT(DISTINCT customer_id) = 0 THEN NULL
         ELSE (SELECT COUNT(*) FROM repeat_customers) * 1.0
              / COUNT(DISTINCT customer_id) END                 AS repeat_customer_rate
FROM filtered;
