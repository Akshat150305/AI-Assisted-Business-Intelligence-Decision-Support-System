"""Data validation rules.

This module only DETECTS problems. It never edits data. Each check is a small function
that returns a boolean mask (True = this row has the problem). The cleaning pipeline
(src/data_pipeline.py) uses these masks to decide what to repair or reject, and
``validate_clean`` re-runs the same checks on the final cleaned table as a safety gate.

Rule catalogue (RULES) is the single place where every rule id is defined.
"""
import numpy as np
import pandas as pd

from app_config import config

# --------------------------------------------------------------------------- rule catalogue
# action: what the cleaning pipeline does when the rule fires.
RULES = {
    "R01": {"name": "exact_duplicate_row", "action": "REMOVED",
            "description": "Row is identical to an earlier row in every column.",
            "decision": "Keep the first copy, quarantine later copies."},
    "R02": {"name": "conflicting_duplicate_key", "action": "REMOVED",
            "description": "Same (order_id, line_number) as an earlier row but with different content.",
            "decision": "Keep the first occurrence, quarantine the others for source review."},
    "R03": {"name": "missing_required_value", "action": "REMOVED",
            "description": "A required field is blank and cannot be rebuilt from other data.",
            "decision": "Quarantine the row."},
    "R04": {"name": "invalid_or_out_of_range_date", "action": "REMOVED",
            "description": "order_date is not a valid YYYY-MM-DD date or lies outside the allowed period.",
            "decision": "Quarantine the row."},
    "R05": {"name": "invalid_quantity", "action": "REMOVED",
            "description": "quantity is not a positive whole number.",
            "decision": "Quarantine the row (the project has no returns, so negative quantity is invalid)."},
    "R06": {"name": "invalid_unit_price", "action": "REMOVED",
            "description": "unit_price is zero, negative or not a number.",
            "decision": "Quarantine the row."},
    "R07": {"name": "revenue_mismatch", "action": "REPAIRED",
            "description": "revenue differs from quantity x unit_price (or is blank).",
            "decision": "Recalculate revenue = quantity x unit_price; log old and new value."},
    "R08": {"name": "price_outlier", "action": "REMOVED",
            "description": "unit_price is far outside the normal range for that product.",
            "decision": "Quarantine the row; cannot tell whether price or quantity is wrong."},
    "R09": {"name": "order_header_conflict", "action": "REMOVED",
            "description": "Lines of one order_id disagree on customer, date or region.",
            "decision": "Keep lines matching the order's most common header, quarantine the rest."},
    "R10": {"name": "profit_mismatch", "action": "REPAIRED",
            "description": "profit differs from revenue - cost (or is blank).",
            "decision": "Recalculate profit = revenue - cost; log old and new value."},
    "R11": {"name": "text_standardised", "action": "REPAIRED",
            "description": "Text has extra spaces or different upper/lower case than the allowed value.",
            "decision": "Trim spaces and map to the allowed spelling."},
    "R12": {"name": "invalid_category_region_segment", "action": "REPAIRED",
            "description": "category, region or customer_segment is not an allowed value.",
            "decision": "Replace using the value this product/customer has in other rows; "
                        "quarantine the row if no reference value exists."},
    "R13": {"name": "missing_value_repaired", "action": "REPAIRED",
            "description": "category, region, customer_segment, product_name or unit_price is blank "
                           "but can be rebuilt.",
            "decision": "Rebuild from the product/customer's other rows, or unit_price = revenue / quantity."},
    "R14": {"name": "invalid_cost", "action": "REMOVED",
            "description": "cost is negative or not a number.",
            "decision": "Quarantine the row."},
}

ALLOWED = {
    "category": config.CATEGORIES,
    "region": config.REGIONS,
    "customer_segment": config.SEGMENTS,
}


# --------------------------------------------------------------------------- parsing helpers
def canonical_map(allowed):
    """{'north': 'North', ...} for case-insensitive matching."""
    return {value.lower(): value for value in allowed}


def parse_dates(series):
    """Strict YYYY-MM-DD parsing. Anything else becomes NaT."""
    return pd.to_datetime(series, format="%Y-%m-%d", errors="coerce")


def parse_numbers(series):
    """Numbers as float. Text that is not a number becomes NaN."""
    return pd.to_numeric(series, errors="coerce")


# --------------------------------------------------------------------------- field checks
# All of these return a boolean Series aligned with the input. They flag only values that are
# PRESENT but wrong. Blank values are a separate problem (missing data).
def date_out_of_range(dates, start=config.START_DATE, end=config.END_DATE):
    return dates.notna() & ((dates < pd.Timestamp(start)) | (dates > pd.Timestamp(end)))


def invalid_quantity(quantity):
    return quantity.notna() & ((quantity <= 0) | (quantity % 1 != 0))


def invalid_price(price):
    return price.notna() & (price <= 0)


def invalid_cost(cost):
    return cost.notna() & (cost < 0)


def not_allowed(series, allowed):
    """Present values that are not exactly one of the allowed values."""
    return series.notna() & ~series.isin(allowed)


def price_outlier(df, band=config.PRICE_BAND):
    """unit_price outside band x the product's median price (median over valid prices)."""
    price = df["unit_price"]
    valid = price.notna() & (price > 0)
    medians = price[valid].groupby(df.loc[valid, "product_id"]).median()
    ratio = price / df["product_id"].map(medians)
    return valid & ratio.notna() & ((ratio < band[0]) | (ratio > band[1]))


def revenue_mismatch(df, tol=config.MONEY_TOLERANCE):
    """revenue present but not equal to quantity x unit_price (only where both inputs are valid)."""
    can_check = df["revenue"].notna() & ~invalid_quantity(df["quantity"]) & df["quantity"].notna() \
        & df["unit_price"].notna() & ~invalid_price(df["unit_price"])
    expected = (df["quantity"] * df["unit_price"]).round(2)
    return can_check & ((df["revenue"] - expected).abs() > tol)


def profit_mismatch(df, tol=config.MONEY_TOLERANCE):
    can_check = df["profit"].notna() & df["revenue"].notna() & df["cost"].notna()
    expected = (df["revenue"] - df["cost"]).round(2)
    return can_check & ((df["profit"] - expected).abs() > tol)


# --------------------------------------------------------------------------- duplicate / order checks
def exact_duplicate(df, columns=None):
    """Rows identical to an EARLIER row in every column (first copy is not flagged)."""
    return df.duplicated(subset=columns or config.SALES_COLUMNS, keep="first")


def duplicate_key(df):
    """Rows whose (order_id, line_number) already appeared earlier. Blank keys are ignored."""
    has_key = df["order_id"].notna() & df["line_number"].notna()
    later_copy = df.loc[has_key].duplicated(subset=["order_id", "line_number"], keep="first")
    return later_copy.reindex(df.index, fill_value=False).astype(bool)


def header_conflict(df):
    """Lines whose (customer, date, region) differs from the order's most common header.

    An order has one customer, one date and one region (requirements assumption A2).
    Ties are resolved in favour of the header that appears first in the data.
    """
    has_id = df["order_id"].notna()
    hdr = pd.DataFrame({
        "order_id": df.loc[has_id, "order_id"].to_numpy(),
        "header": (df.loc[has_id, "customer_id"].astype(str) + "|"
                   + df.loc[has_id, "order_date"].astype(str) + "|"
                   + df.loc[has_id, "region"].astype(str)).to_numpy(),
        "pos": np.arange(int(has_id.sum())),
    })
    counts = hdr.groupby(["order_id", "header"]).agg(n=("pos", "size"), first=("pos", "min")).reset_index()
    modal = (counts.sort_values(["order_id", "n", "first"], ascending=[True, False, True])
             .drop_duplicates("order_id").set_index("order_id")["header"])
    conflict = (hdr["header"].to_numpy() != hdr["order_id"].map(modal).to_numpy())
    flagged = pd.Series(False, index=df.index, dtype=bool)
    flagged[has_id.to_numpy()] = conflict
    return flagged


# --------------------------------------------------------------------------- final safety gate
def validate_clean(df):
    """Run every check on a cleaned table. Returns a DataFrame of problems (empty = clean).

    Columns: rule_id, rule_name, rows_affected, example_rows
    """
    problems = []

    def add(rule_id, mask):
        n = int(mask.sum())
        if n:
            problems.append({"rule_id": rule_id, "rule_name": RULES[rule_id]["name"],
                             "rows_affected": n, "example_rows": list(df.index[mask][:5])})

    if len(df):
        add("R03", df[config.SALES_COLUMNS].isna().any(axis=1))
        dates = df["order_date"] if pd.api.types.is_datetime64_any_dtype(df["order_date"]) \
            else parse_dates(df["order_date"])
        add("R04", dates.isna() | date_out_of_range(dates))
        add("R05", invalid_quantity(df["quantity"]))
        add("R06", invalid_price(df["unit_price"]))
        add("R14", invalid_cost(df["cost"]))
        add("R08", price_outlier(df))
        add("R07", revenue_mismatch(df))
        add("R10", profit_mismatch(df))
        add("R01", exact_duplicate(df))
        add("R02", duplicate_key(df))
        add("R09", header_conflict(df.assign(order_date=dates)))
        for column, allowed in ALLOWED.items():
            add("R12", not_allowed(df[column], allowed))
    return pd.DataFrame(problems, columns=["rule_id", "rule_name", "rows_affected", "example_rows"])
