-- One row per customer: purchasing behaviour inside the selected filters.
-- Parameters (NULL = no restriction): :start_date :end_date :region :category :segment
--
-- distinct_orders counts ORDERS (not lines). is_repeat_customer = 1 when the customer placed
-- 2 or more distinct orders inside the filtered selection.
-- This table does NOT label customers as churned or at risk (no defensible definition exists yet).
WITH filtered AS (
    SELECT *
    FROM sales_lines
    WHERE (:start_date IS NULL OR order_date >= :start_date)
      AND (:end_date   IS NULL OR order_date <= :end_date)
      AND (:region     IS NULL OR region = :region)
      AND (:category   IS NULL OR category = :category)
      AND (:segment    IS NULL OR customer_segment = :segment)
)
SELECT
    customer_id,
    customer_segment,
    region,
    COUNT(DISTINCT order_id)                                       AS distinct_orders,
    COUNT(*)                                                       AS order_lines,
    SUM(quantity)                                                  AS units_sold,
    SUM(revenue)                                                   AS revenue,
    SUM(profit)                                                    AS profit,
    CASE WHEN SUM(revenue) = 0 THEN NULL
         ELSE SUM(profit) * 100.0 / SUM(revenue) END               AS profit_margin_pct,
    SUM(revenue) * 1.0 / COUNT(DISTINCT order_id)                  AS avg_order_value,
    MIN(order_date)                                                AS first_order_date,
    MAX(order_date)                                                AS last_order_date,
    CASE WHEN COUNT(DISTINCT order_id) >= 2 THEN 1 ELSE 0 END      AS is_repeat_customer
FROM filtered
GROUP BY customer_id, customer_segment, region
ORDER BY revenue DESC, customer_id ASC;
