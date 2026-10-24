-- Revenue and profit by month, with month-over-month change.
-- Parameters (NULL = no restriction): :start_date :end_date :region :category :segment
--
-- Change % = (this month - previous month) / ABS(previous month) * 100.
-- It is only calculated when the previous CALENDAR month is present in the filtered data.
-- (Without that check, LAG() would silently compare March with January when February has no rows.)
WITH filtered AS (
    SELECT *
    FROM sales_lines
    WHERE (:start_date IS NULL OR order_date >= :start_date)
      AND (:end_date   IS NULL OR order_date <= :end_date)
      AND (:region     IS NULL OR region = :region)
      AND (:category   IS NULL OR category = :category)
      AND (:segment    IS NULL OR customer_segment = :segment)
),
monthly AS (
    SELECT
        strftime('%Y-%m', order_date)                                   AS month,
        CAST(strftime('%Y', order_date) AS INTEGER) * 12
          + CAST(strftime('%m', order_date) AS INTEGER)                 AS month_index,
        SUM(revenue)                                                    AS revenue,
        SUM(cost)                                                       AS cost,
        SUM(profit)                                                     AS profit,
        COUNT(DISTINCT order_id)                                        AS distinct_orders,
        COUNT(*)                                                        AS order_lines,
        SUM(quantity)                                                   AS units_sold
    FROM filtered
    GROUP BY month, month_index
),
with_previous AS (
    SELECT
        monthly.*,
        LAG(month_index) OVER (ORDER BY month_index) AS prev_index,
        LAG(revenue)     OVER (ORDER BY month_index) AS prev_revenue,
        LAG(profit)      OVER (ORDER BY month_index) AS prev_profit
    FROM monthly
)
SELECT
    month,
    revenue,
    cost,
    profit,
    CASE WHEN revenue = 0 THEN NULL ELSE profit * 100.0 / revenue END   AS profit_margin_pct,
    distinct_orders,
    order_lines,
    units_sold,
    revenue * 1.0 / distinct_orders                                     AS avg_order_value,
    CASE WHEN prev_index = month_index - 1 AND prev_revenue <> 0
         THEN (revenue - prev_revenue) * 100.0 / ABS(prev_revenue) END  AS revenue_change_pct,
    CASE WHEN prev_index = month_index - 1 AND prev_profit <> 0
         THEN (profit - prev_profit) * 100.0 / ABS(prev_profit) END     AS profit_change_pct
FROM with_previous
ORDER BY month;
