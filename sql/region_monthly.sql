-- Revenue, profit and margin by region AND month (for regional trend charts).
-- Parameters (NULL = no restriction): :start_date :end_date :region :category :segment
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
    region,
    strftime('%Y-%m', order_date)                                  AS month,
    SUM(revenue)                                                   AS revenue,
    SUM(cost)                                                      AS cost,
    SUM(profit)                                                    AS profit,
    CASE WHEN SUM(revenue) = 0 THEN NULL
         ELSE SUM(profit) * 100.0 / SUM(revenue) END               AS profit_margin_pct,
    COUNT(DISTINCT order_id)                                       AS distinct_orders,
    SUM(revenue) * 1.0 / COUNT(DISTINCT order_id)                  AS avg_order_value
FROM filtered
GROUP BY region, month
ORDER BY region, month;
