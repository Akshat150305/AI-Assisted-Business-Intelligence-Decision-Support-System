-- Schema for the retail BI database (SQLite).
-- ALL DATA IS SYNTHETIC.
--
-- Design:
--   sales_lines   = one row per ORDER LINE (the grain). All SQL analysis queries read this one table.
--                   It is deliberately "wide" (region, category ... repeated on every row) so the queries
--                   stay simple and every filter is one WHERE clause.
--   dim_product / dim_customer = reference tables. They enforce that one product has one category and one
--                   customer has one segment and one region (requirements assumptions A3 and A4).
--                   The Power BI exports use these as dimension tables (star schema).
--
-- Dates are stored as TEXT in ISO format YYYY-MM-DD, which sorts and compares correctly as text.

PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS sales_lines;
DROP TABLE IF EXISTS dim_customer;
DROP TABLE IF EXISTS dim_product;

CREATE TABLE dim_product (
    product_id    TEXT PRIMARY KEY,
    product_name  TEXT NOT NULL,
    category      TEXT NOT NULL
);

CREATE TABLE dim_customer (
    customer_id       TEXT PRIMARY KEY,
    customer_segment  TEXT NOT NULL,
    region            TEXT NOT NULL
);

CREATE TABLE sales_lines (
    order_id          TEXT    NOT NULL,
    line_number       INTEGER NOT NULL CHECK (line_number >= 1),
    order_date        TEXT    NOT NULL
                      CHECK (order_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    customer_id       TEXT    NOT NULL REFERENCES dim_customer (customer_id),
    customer_segment  TEXT    NOT NULL,
    product_id        TEXT    NOT NULL REFERENCES dim_product (product_id),
    product_name      TEXT    NOT NULL,
    category          TEXT    NOT NULL,
    region            TEXT    NOT NULL,
    quantity          INTEGER NOT NULL CHECK (quantity > 0),
    unit_price        REAL    NOT NULL CHECK (unit_price > 0),
    revenue           REAL    NOT NULL,   -- quantity x unit_price
    cost              REAL    NOT NULL CHECK (cost >= 0),   -- total cost of the line
    profit            REAL    NOT NULL,   -- revenue - cost
    PRIMARY KEY (order_id, line_number)
);

CREATE INDEX idx_sales_date     ON sales_lines (order_date);
CREATE INDEX idx_sales_region   ON sales_lines (region);
CREATE INDEX idx_sales_category ON sales_lines (category);
CREATE INDEX idx_sales_customer ON sales_lines (customer_id);
CREATE INDEX idx_sales_product  ON sales_lines (product_id);
