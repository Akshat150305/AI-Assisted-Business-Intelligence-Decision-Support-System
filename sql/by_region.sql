-- Revenue, profit and margin by region.
-- Parameters (NULL = no restriction): :start_date :end_date :region :category :segment
--
-- revenue_share_pct / profit_share_pct = this row's share of the total of the SAME filtered selection.
-- Ties in revenue are ordered by the name/key so the output order is always the same.
WITH filtered AS (
    SELECT *
    FROM sales_lines
    WHERE (:start_date IS NULL OR order_date >= :start_date)
      AND (:end_date   IS NULL OR order_date <= :end_date)
      AND (:region     IS NULL OR region = :region)
      AND (:category   IS NULL OR category = :category)
      AND (:segment    IS NULL OR customer_segment = :segment)
),
totals AS (
    SELECT SUM(revenue) AS total_revenue, SUM(profit) AS total_profit FROM filtered
)
SELECT
    region,
    SUM(revenue)                                                   AS revenue,
    SUM(cost)                                                      AS cost,
    SUM(profit)                                                    AS profit,
    CASE WHEN SUM(revenue) = 0 THEN NULL
         ELSE SUM(profit) * 100.0 / SUM(revenue) END               AS profit_margin_pct,
    COUNT(DISTINCT order_id)                                       AS distinct_orders,
    COUNT(*)                                                       AS order_lines,
    SUM(quantity)                                                  AS units_sold,
    SUM(revenue) * 1.0 / COUNT(DISTINCT order_id)                  AS avg_order_value,
    CASE WHEN MAX(totals.total_revenue) = 0 THEN NULL
         ELSE SUM(revenue) * 100.0 / MAX(totals.total_revenue) END AS revenue_share_pct,
    CASE WHEN MAX(totals.total_profit) = 0 THEN NULL
         ELSE SUM(profit) * 100.0 / MAX(totals.total_profit) END   AS profit_share_pct
FROM filtered
CROSS JOIN totals
GROUP BY region
ORDER BY revenue DESC, region ASC;
