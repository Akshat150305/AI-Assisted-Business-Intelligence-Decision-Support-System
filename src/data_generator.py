"""Synthetic retail data generator.

ALL DATA PRODUCED HERE IS SYNTHETIC. It is generated from a fixed random seed and
does not represent any real company.

The generator produces three kinds of things, deliberately kept apart:

1. ``sales_truth``      - the clean, correct order lines (one row = one order line).
2. ``raw``              - the same data after planting known data-quality defects
                          (missing values, duplicates, bad dates ...). This is the file the
                          pipeline will receive: data/raw/sales_raw.csv
3. Label files          - ``injected_anomalies.csv`` (planted business anomalies, for testing
                          anomaly detection) and ``injected_defects.csv`` (planted data defects,
                          for testing validation). These are NEVER used as algorithm input.

Run from the project root:   python -m src.data_generator
"""
import argparse

import numpy as np
import pandas as pd

from app_config import config

# --------------------------------------------------------------------------- reference data
# (product_id, product_name, category, base_price, cost_ratio)
# cost_ratio = typical unit cost as a share of price, so margin is roughly 1 - cost_ratio.
PRODUCTS = [
    ("P001", "Wireless Earbuds", "Electronics", 59.99, 0.78),
    ("P002", "Bluetooth Speaker", "Electronics", 49.99, 0.76),
    ("P003", "Smart Watch", "Electronics", 129.99, 0.82),
    ("P004", "Power Bank", "Electronics", 24.99, 0.72),
    ("P005", "USB-C Charger", "Electronics", 19.99, 0.65),
    ("P006", "HD Webcam", "Electronics", 44.99, 0.74),
    ("P007", "Mechanical Keyboard", "Electronics", 89.99, 0.75),
    ("P008", "Gaming Mouse", "Electronics", 39.99, 0.70),
    ("P009", "Air Fryer", "Home & Kitchen", 89.99, 0.68),
    ("P010", "Non-stick Pan Set", "Home & Kitchen", 59.99, 0.60),
    ("P011", "Electric Kettle", "Home & Kitchen", 29.99, 0.62),
    ("P012", "Blender", "Home & Kitchen", 49.99, 0.65),
    ("P013", "Knife Set", "Home & Kitchen", 39.99, 0.55),
    ("P014", "Storage Containers", "Home & Kitchen", 24.99, 0.50),
    ("P015", "Coffee Maker", "Home & Kitchen", 74.99, 0.66),
    ("P016", "Bed Sheet Set", "Home & Kitchen", 34.99, 0.52),
    ("P017", "Cotton T-Shirt", "Clothing", 14.99, 0.42),
    ("P018", "Denim Jeans", "Clothing", 49.99, 0.50),
    ("P019", "Winter Jacket", "Clothing", 99.99, 0.55),
    ("P020", "Running Shorts", "Clothing", 19.99, 0.44),
    ("P021", "Hoodie", "Clothing", 39.99, 0.48),
    ("P022", "Formal Shirt", "Clothing", 34.99, 0.46),
    ("P023", "Wool Socks", "Clothing", 9.99, 0.40),
    ("P024", "Sneakers", "Clothing", 69.99, 0.58),
    ("P025", "Yoga Mat", "Sports & Outdoors", 24.99, 0.52),
    ("P026", "Dumbbell Set", "Sports & Outdoors", 79.99, 0.68),
    ("P027", "Camping Tent", "Sports & Outdoors", 119.99, 0.70),
    ("P028", "Water Bottle", "Sports & Outdoors", 14.99, 0.45),
    ("P029", "Football", "Sports & Outdoors", 22.99, 0.55),
    ("P030", "Cycling Helmet", "Sports & Outdoors", 44.99, 0.62),
    ("P031", "Resistance Bands", "Sports & Outdoors", 12.99, 0.45),
    ("P032", "Hiking Backpack", "Sports & Outdoors", 64.99, 0.60),
    ("P033", "Face Moisturizer", "Beauty & Personal Care", 24.99, 0.38),
    ("P034", "Shampoo", "Beauty & Personal Care", 12.99, 0.40),
    ("P035", "Perfume", "Beauty & Personal Care", 59.99, 0.42),
    ("P036", "Electric Toothbrush", "Beauty & Personal Care", 34.99, 0.55),
    ("P037", "Hair Dryer", "Beauty & Personal Care", 44.99, 0.60),
    ("P038", "Sunscreen", "Beauty & Personal Care", 14.99, 0.40),
    ("P039", "Lipstick Set", "Beauty & Personal Care", 19.99, 0.35),
    ("P040", "Beard Trimmer", "Beauty & Personal Care", 29.99, 0.58),
]

# Share of line items going to each category (before seasonality).
CATEGORY_WEIGHT = {
    "Electronics": 0.25,
    "Home & Kitchen": 0.20,
    "Clothing": 0.25,
    "Sports & Outdoors": 0.15,
    "Beauty & Personal Care": 0.15,
}

# Category demand by calendar month (1.0 = normal). Months not listed default to 1.0.
CATEGORY_SEASONALITY = {
    "Electronics": {11: 1.5, 12: 1.3},
    "Clothing": {10: 1.2, 11: 1.3, 12: 1.3, 6: 0.9, 7: 0.9},
    "Sports & Outdoors": {4: 1.2, 5: 1.25, 6: 1.2},
    "Beauty & Personal Care": {12: 1.2},
    "Home & Kitchen": {10: 1.1},
}

# Overall order volume by calendar month and by weekday (Monday = 0).
MONTH_VOLUME = [0.95, 0.92, 1.00, 1.00, 1.02, 0.98, 0.97, 1.00, 1.02, 1.08, 1.20, 1.30]
WEEKDAY_VOLUME = [0.95, 0.95, 0.97, 1.00, 1.10, 1.20, 1.05]

# Built-in regional patterns (simulated, so analysis has something real to discover).
#   share        : share of orders at the start of the period
#   growth       : extra monthly growth rate of order volume for the region
#   price_factor : average selling price relative to list price
REGION_PROFILE = {
    "North": {"share": 0.27, "growth": 0.000, "price_factor": 1.00},
    "South": {"share": 0.25, "growth": 0.008, "price_factor": 1.00},
    "East": {"share": 0.20, "growth": -0.015, "price_factor": 0.96},
    "West": {"share": 0.28, "growth": 0.020, "price_factor": 1.01},
}
GLOBAL_MONTHLY_GROWTH = 0.003

# Customer segment behaviour.
#   share         : share of customers
#   propensity    : how often the segment orders relative to Consumer
#   extra_lines   : added to the average number of lines per order
#   qty_lambda    : quantity per line = 1 + Poisson(qty_lambda)
SEGMENT_PROFILE = {
    "Consumer": {"share": 0.65, "propensity": 1.0, "extra_lines": 0.0, "qty_lambda": 0.5},
    "Small Business": {"share": 0.25, "propensity": 1.5, "extra_lines": 0.4, "qty_lambda": 1.5},
    "Corporate": {"share": 0.10, "propensity": 2.5, "extra_lines": 0.9, "qty_lambda": 4.0},
}

# --------------------------------------------------------------------------- planted business anomalies
# These are SIMULATED events. The generator only models that something unusual happened,
# not why. Labels are saved separately and are only used to evaluate the detector.
ANOMALIES = [
    {
        "anomaly_id": "A1", "anomaly_type": "revenue_spike", "kind": "volume",
        "start": "2025-03-17", "end": "2025-03-23", "region": "South", "category": "",
        "affected_metric": "revenue", "multiplier": 2.5,
        "description": "South order volume multiplied by 2.5 for one week (simulated event).",
    },
    {
        "anomaly_id": "A2", "anomaly_type": "revenue_drop", "kind": "volume",
        "start": "2025-06-09", "end": "2025-06-15", "region": "West", "category": "",
        "affected_metric": "revenue", "multiplier": 0.35,
        "description": "West order volume cut to 35% for one week (simulated event).",
    },
    {
        "anomaly_id": "A3", "anomaly_type": "margin_squeeze", "kind": "cost",
        "start": "2025-09-01", "end": "2025-09-28", "region": "", "category": "Electronics",
        "affected_metric": "profit_margin", "multiplier": 1.35,
        "description": "Electronics unit cost multiplied by 1.35 for four weeks; revenue unaffected, so the effect "
                       "shows in profit margin % (simulated event).",
    },
    {
        "anomaly_id": "A4", "anomaly_type": "single_large_order", "kind": "bulk_order",
        "start": "2025-11-12", "end": "2025-11-12", "region": "", "category": "",
        "affected_metric": "revenue", "multiplier": 25.0,
        "description": "Quantities of one Corporate order multiplied by 25 (simulated event).",
    },
]

# --------------------------------------------------------------------------- planted data defects
# (defect_type, number of rows, expected cleaning action, expected rule id)
# Rule ids are defined in src/validation.py (RULES).
DEFECT_PLAN = [
    ("missing_customer_id", 12, "REMOVED", "R03"),
    ("missing_order_id", 3, "REMOVED", "R03"),
    ("missing_quantity", 3, "REMOVED", "R03"),
    ("missing_cost", 3, "REMOVED", "R03"),
    ("missing_customer_segment", 10, "REPAIRED", "R13"),
    ("missing_category", 10, "REPAIRED", "R13"),
    ("missing_region", 10, "REPAIRED", "R13"),
    ("missing_unit_price", 8, "REPAIRED", "R13"),
    ("invalid_date_text", 6, "REMOVED", "R04"),
    ("future_date", 4, "REMOVED", "R04"),
    ("negative_quantity", 5, "REMOVED", "R05"),
    ("zero_quantity", 3, "REMOVED", "R05"),
    ("nonpositive_unit_price", 4, "REMOVED", "R06"),
    ("price_outlier", 4, "REMOVED", "R08"),
    ("negative_cost", 3, "REMOVED", "R14"),
    ("revenue_mismatch", 10, "REPAIRED", "R07"),
    ("profit_mismatch", 10, "REPAIRED", "R10"),
    ("category_case_whitespace", 10, "REPAIRED", "R11"),
    ("region_case_whitespace", 10, "REPAIRED", "R11"),
    ("invalid_category", 8, "REPAIRED", "R12"),
    ("invalid_region", 8, "REPAIRED", "R12"),
    ("invalid_segment", 6, "REPAIRED", "R12"),
    ("exact_duplicate_row", 20, "REMOVED", "R01"),
    ("conflicting_duplicate_key", 8, "REMOVED", "R02"),
    ("order_header_conflict", 4, "REMOVED", "R09"),
]

BAD_DATES = ["2025-13-40", "31/02/2025", "N/A", "yesterday", "2025-02-30"]
BAD_CATEGORIES = ["Electroincs", "Misc", "Unknown", "Hme & Kitchen"]
BAD_REGIONS = ["Nrth", "Unknown", "Central", "N/A"]
BAD_SEGMENTS = ["SMB", "Enterprise", "Unknown"]


# --------------------------------------------------------------------------- clean data
def build_products():
    return pd.DataFrame(
        PRODUCTS,
        columns=["product_id", "product_name", "category", "base_price", "cost_ratio"],
    )


def build_customers(rng, n_customers):
    """Customer master: each customer has ONE segment and ONE region (requirements A3)."""
    regions = list(REGION_PROFILE)
    segments = list(SEGMENT_PROFILE)
    region = rng.choice(regions, size=n_customers, p=[REGION_PROFILE[r]["share"] for r in regions])
    segment = rng.choice(segments, size=n_customers, p=[SEGMENT_PROFILE[s]["share"] for s in segments])
    # Lognormal propensity -> a few customers order far more often than most (concentration).
    propensity = rng.lognormal(mean=0.0, sigma=0.9, size=n_customers)
    propensity = propensity * np.array([SEGMENT_PROFILE[s]["propensity"] for s in segment])
    return pd.DataFrame({
        "customer_id": [f"C{i:04d}" for i in range(1, n_customers + 1)],
        "segment": segment,
        "region": region,
        "propensity": propensity,
    })


def _generate_orders(rng, customers, dates, base_orders_per_day, volume_anomalies):
    """One row per order: date, region, customer."""
    t = np.arange(len(dates)) / 30.4375                      # months since start
    weekday = np.array(WEEKDAY_VOLUME)[dates.day_of_week]
    month = np.array(MONTH_VOLUME)[dates.month - 1]
    blocks = []
    for region, prof in REGION_PROFILE.items():
        lam = (
            base_orders_per_day * prof["share"]
            * (1 + prof["growth"] + GLOBAL_MONTHLY_GROWTH) ** t
            * weekday * month
        )
        for a in volume_anomalies:
            if a["region"] == region:
                in_window = (dates >= pd.Timestamp(a["start"])) & (dates <= pd.Timestamp(a["end"]))
                lam = np.where(in_window, lam * a["multiplier"], lam)
        counts = rng.poisson(lam)
        pool = customers[customers["region"] == region]
        p = pool["propensity"].to_numpy()
        chosen = rng.choice(pool["customer_id"].to_numpy(), size=int(counts.sum()), p=p / p.sum())
        blocks.append(pd.DataFrame({
            "order_date": np.repeat(dates.to_numpy(), counts),
            "region": region,
            "customer_id": chosen,
        }))
    orders = pd.concat(blocks, ignore_index=True)
    orders = orders.sort_values("order_date", kind="stable").reset_index(drop=True)
    orders["order_id"] = [f"ORD-{i:06d}" for i in range(1, len(orders) + 1)]
    seg_lookup = customers.set_index("customer_id")["segment"]
    orders["segment"] = orders["customer_id"].map(seg_lookup)
    return orders


def generate_clean_sales(
    seed=config.SEED,
    start=config.START_DATE,
    end=config.END_DATE,
    n_customers=config.N_CUSTOMERS,
    base_orders_per_day=config.BASE_ORDERS_PER_DAY,
    include_anomalies=True,
):
    """Return (sales, anomaly_labels). ``sales`` is the clean ground truth."""
    rng = np.random.default_rng(seed)
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    dates = pd.date_range(start_ts, end_ts, freq="D")
    products = build_products()
    customers = build_customers(rng, n_customers)

    # Only plant anomalies whose whole window lies inside the generated period.
    active = []
    if include_anomalies:
        for a in ANOMALIES:
            if pd.Timestamp(a["start"]) >= start_ts and pd.Timestamp(a["end"]) <= end_ts:
                active.append(dict(a))

    orders = _generate_orders(rng, customers, dates, base_orders_per_day,
                              [a for a in active if a["kind"] == "volume"])

    # Bulk-order anomaly: pick one existing order (prefer a Corporate customer).
    for a in active:
        if a["kind"] == "bulk_order":
            day = orders[orders["order_date"] == pd.Timestamp(a["start"])]
            corp = day[day["segment"] == "Corporate"]
            pick = corp if len(corp) else day
            a["affected_order_id"] = pick["order_id"].iloc[0] if len(pick) else ""
        else:
            a["affected_order_id"] = ""
    active = [a for a in active if a["kind"] != "bulk_order" or a["affected_order_id"]]

    # ---- expand orders into order lines
    seg_extra = orders["segment"].map({s: p["extra_lines"] for s, p in SEGMENT_PROFILE.items()}).to_numpy()
    n_lines = np.minimum(1 + rng.poisson(0.9 + seg_extra), 6)
    lines = orders.loc[orders.index.repeat(n_lines)].reset_index(drop=True)
    offsets = np.cumsum(n_lines) - n_lines
    lines["line_number"] = np.arange(len(lines)) - np.repeat(offsets, n_lines) + 1
    n = len(lines)

    # ---- product choice depends on the month (category seasonality)
    n_products = len(products)
    cat_count = products["category"].value_counts()
    popularity = rng.lognormal(mean=0.0, sigma=0.5, size=n_products)
    base_weight = np.array([
        popularity[i] * CATEGORY_WEIGHT[c] / cat_count[c] for i, c in enumerate(products["category"])
    ])
    year_month = (lines["order_date"].dt.year * 100 + lines["order_date"].dt.month).to_numpy()
    product_idx = np.empty(n, dtype=int)
    for key in np.unique(year_month):
        month_no = int(key % 100)
        w = base_weight * np.array([
            CATEGORY_SEASONALITY.get(c, {}).get(month_no, 1.0) for c in products["category"]
        ])
        mask = year_month == key
        product_idx[mask] = rng.choice(n_products, size=int(mask.sum()), p=w / w.sum())

    # ---- quantity, price, cost
    quantity = 1 + rng.poisson(lines["segment"].map({s: p["qty_lambda"] for s, p in SEGMENT_PROFILE.items()}).to_numpy())
    progress = ((lines["order_date"] - start_ts).dt.days / max(len(dates) - 1, 1)).to_numpy()
    base_price = products["base_price"].to_numpy()[product_idx]
    cost_ratio = products["cost_ratio"].to_numpy()[product_idx]
    region_factor = lines["region"].map({r: p["price_factor"] for r, p in REGION_PROFILE.items()}).to_numpy()
    price_noise = np.clip(rng.normal(1.0, 0.02, n), 0.93, 1.07)
    cost_noise = np.clip(rng.normal(1.0, 0.015, n), 0.95, 1.05)
    unit_price = np.round(base_price * region_factor * (1 + 0.03 * progress) * price_noise, 2)
    unit_cost = base_price * cost_ratio * (1 + 0.02 * progress) * cost_noise

    category = products["category"].to_numpy()[product_idx]
    for a in active:
        if a["kind"] == "cost":
            in_window = ((lines["order_date"] >= pd.Timestamp(a["start"]))
                         & (lines["order_date"] <= pd.Timestamp(a["end"]))).to_numpy()
            hit = in_window & (category == a["category"])
            unit_cost = np.where(hit, unit_cost * a["multiplier"], unit_cost)
        if a["kind"] == "bulk_order":
            hit = (lines["order_id"] == a["affected_order_id"]).to_numpy()
            quantity = np.where(hit, quantity * int(a["multiplier"]), quantity)

    revenue = np.round(quantity * unit_price, 2)
    cost = np.round(quantity * unit_cost, 2)
    sales = pd.DataFrame({
        "order_id": lines["order_id"],
        "line_number": lines["line_number"].astype(int),
        "order_date": lines["order_date"],
        "customer_id": lines["customer_id"],
        "customer_segment": lines["segment"],
        "product_id": products["product_id"].to_numpy()[product_idx],
        "product_name": products["product_name"].to_numpy()[product_idx],
        "category": category,
        "region": lines["region"],
        "quantity": quantity.astype(int),
        "unit_price": unit_price,
        "revenue": revenue,
        "cost": cost,
        "profit": np.round(revenue - cost, 2),
    })[config.SALES_COLUMNS]

    labels = pd.DataFrame(
        [{k: a[k] for k in ("anomaly_id", "anomaly_type", "start", "end", "region", "category",
                            "affected_metric", "multiplier", "affected_order_id", "description")}
         for a in active],
        columns=["anomaly_id", "anomaly_type", "start", "end", "region", "category",
                 "affected_metric", "multiplier", "affected_order_id", "description"],
    ).rename(columns={"start": "start_date", "end": "end_date"})
    return sales, labels


# --------------------------------------------------------------------------- raw data (planted defects)
def _scramble_case(value, rng):
    """Change case / add whitespace so the value is still recognisable."""
    choice = int(rng.integers(0, 4))
    return [value.lower(), value.upper(), " " + value, value + "  "][choice]


def inject_defects(sales, seed=config.SEED):
    """Plant documented data-quality defects into a copy of the clean data.

    Every defect row is chosen from a fresh random permutation without overlap, so each
    planted defect is independent and the expected cleaning outcome is unambiguous.
    Returns (raw, defect_labels).
    """
    rng = np.random.default_rng(seed + 1)                    # separate stream from sales generation
    df = sales.astype(object).copy()
    df["order_date"] = sales["order_date"].dt.strftime("%Y-%m-%d").astype(object)
    df["_uid"] = np.arange(len(df), dtype=float)

    perm = rng.permutation(len(df))
    cursor = 0
    # Header and size information for the order_header_conflict defect (taken before any edit).
    order_size = sales.groupby("order_id").size()
    big_orders = order_size[order_size >= 4].index.to_numpy()
    header = sales.groupby("order_id").agg(customer_id=("customer_id", "first"),
                                           order_date=("order_date", "first"))
    next_line = sales.groupby("order_id")["line_number"].max().to_dict()

    extra_rows, records = [], []                              # records: (uid, type, description, action, rule)

    for defect_type, count, action, rule in DEFECT_PLAN:
        for i in perm[cursor:cursor + count]:
            i = int(i)
            uid = float(df.at[i, "_uid"])
            if defect_type == "missing_customer_id":
                df.at[i, "customer_id"] = np.nan; desc = "customer_id blanked"
            elif defect_type == "missing_order_id":
                df.at[i, "order_id"] = np.nan; desc = "order_id blanked"
            elif defect_type == "missing_quantity":
                df.at[i, "quantity"] = np.nan; desc = "quantity blanked"
            elif defect_type == "missing_cost":
                df.at[i, "cost"] = np.nan; desc = "cost blanked"
            elif defect_type == "missing_customer_segment":
                df.at[i, "customer_segment"] = np.nan; desc = "customer_segment blanked"
            elif defect_type == "missing_category":
                df.at[i, "category"] = np.nan; desc = "category blanked"
            elif defect_type == "missing_region":
                df.at[i, "region"] = np.nan; desc = "region blanked"
            elif defect_type == "missing_unit_price":
                df.at[i, "unit_price"] = np.nan; desc = "unit_price blanked"
            elif defect_type == "invalid_date_text":
                bad = str(rng.choice(BAD_DATES)); df.at[i, "order_date"] = bad
                desc = f"order_date replaced by '{bad}'"
            elif defect_type == "future_date":
                bad = f"{int(rng.integers(2030, 2033))}-{int(rng.integers(1, 13)):02d}-{int(rng.integers(1, 29)):02d}"
                df.at[i, "order_date"] = bad; desc = f"order_date set to future date {bad}"
            elif defect_type == "negative_quantity":
                df.at[i, "quantity"] = -int(df.at[i, "quantity"]); desc = "quantity made negative"
            elif defect_type == "zero_quantity":
                df.at[i, "quantity"] = 0; desc = "quantity set to 0"
            elif defect_type == "nonpositive_unit_price":
                df.at[i, "unit_price"] = float(rng.choice([0.0, -float(df.at[i, "unit_price"])]))
                desc = "unit_price set to zero or negative"
            elif defect_type == "price_outlier":
                df.at[i, "unit_price"] = round(float(df.at[i, "unit_price"]) * 100, 2)
                df.at[i, "revenue"] = round(float(df.at[i, "revenue"]) * 100, 2)
                desc = "unit_price and revenue multiplied by 100 (decimal slip)"
            elif defect_type == "negative_cost":
                df.at[i, "cost"] = -float(df.at[i, "cost"]); desc = "cost made negative"
            elif defect_type == "revenue_mismatch":
                factor = float(rng.choice([rng.uniform(1.1, 1.3), rng.uniform(0.7, 0.9)]))
                df.at[i, "revenue"] = round(float(df.at[i, "revenue"]) * factor, 2)
                desc = "revenue changed so it no longer equals quantity x unit_price"
            elif defect_type == "profit_mismatch":
                shift = float(rng.uniform(5, 50)) * float(rng.choice([-1, 1]))
                df.at[i, "profit"] = round(float(df.at[i, "profit"]) + shift, 2)
                desc = "profit changed so it no longer equals revenue - cost"
            elif defect_type == "category_case_whitespace":
                df.at[i, "category"] = _scramble_case(df.at[i, "category"], rng); desc = "category case/whitespace changed"
            elif defect_type == "region_case_whitespace":
                df.at[i, "region"] = _scramble_case(df.at[i, "region"], rng); desc = "region case/whitespace changed"
            elif defect_type == "invalid_category":
                bad = str(rng.choice(BAD_CATEGORIES)); df.at[i, "category"] = bad
                desc = f"category replaced by '{bad}'"
            elif defect_type == "invalid_region":
                bad = str(rng.choice(BAD_REGIONS)); df.at[i, "region"] = bad
                desc = f"region replaced by '{bad}'"
            elif defect_type == "invalid_segment":
                bad = str(rng.choice(BAD_SEGMENTS)); df.at[i, "customer_segment"] = bad
                desc = f"customer_segment replaced by '{bad}'"
            elif defect_type in ("exact_duplicate_row", "conflicting_duplicate_key"):
                copy = df.loc[i].copy()
                copy["_uid"] = uid + 0.5                      # copy is placed right after the original
                if defect_type == "conflicting_duplicate_key":
                    copy["quantity"] = int(copy["quantity"]) + int(rng.integers(1, 4))
                    desc = "copy of an existing order line key with a different quantity"
                else:
                    desc = "exact copy of an existing row"
                extra_rows.append(copy)
                uid = uid + 0.5                               # the defect row is the copy
            elif defect_type == "order_header_conflict":
                src_order, src_cust = df.at[i, "order_id"], df.at[i, "customer_id"]
                src_date = df.at[i, "order_date"]
                for _ in range(100):
                    target = str(rng.choice(big_orders))
                    if (target != src_order and header.at[target, "customer_id"] != src_cust
                            and header.at[target, "order_date"].strftime("%Y-%m-%d") != src_date):
                        break
                next_line[target] += 1
                df.at[i, "order_id"] = target
                df.at[i, "line_number"] = next_line[target]
                desc = f"line moved into order {target} (different customer and date)"
            else:                                             # pragma: no cover
                raise ValueError(f"Unknown defect type {defect_type}")
            records.append((uid, defect_type, desc, action, rule))
        cursor += count

    out = pd.concat([df, pd.DataFrame(extra_rows)], ignore_index=True) if extra_rows else df
    out = out.sort_values("_uid", kind="stable").reset_index(drop=True)
    out["source_row"] = np.arange(1, len(out) + 1)
    uid_to_row = dict(zip(out["_uid"].astype(float), out["source_row"]))

    defects = pd.DataFrame(records, columns=["uid", "defect_type", "description",
                                             "expected_action", "expected_rule"])
    defects["source_row"] = defects["uid"].map(uid_to_row).astype(int)
    row_info = out.set_index("source_row")[["order_id", "line_number"]]
    defects["order_id"] = defects["source_row"].map(row_info["order_id"])
    defects["line_number"] = defects["source_row"].map(row_info["line_number"])
    defects = defects.sort_values("source_row").reset_index(drop=True)
    defects.insert(0, "defect_id", [f"D{i:03d}" for i in range(1, len(defects) + 1)])
    defects = defects[["defect_id", "source_row", "order_id", "line_number", "defect_type",
                       "description", "expected_action", "expected_rule"]]

    raw = out[config.SALES_COLUMNS].copy()
    for col in ("unit_price", "revenue", "cost", "profit"):
        raw[col] = pd.to_numeric(raw[col]).astype(float)
    for col in ("quantity", "line_number"):
        raw[col] = pd.to_numeric(raw[col]).astype("Int64")
    return raw, defects


# --------------------------------------------------------------------------- orchestration
def build_datasets(seed=config.SEED, **kwargs):
    """Build everything in memory. Returns a dict with the truth, raw data and both label tables."""
    truth, anomaly_labels = generate_clean_sales(seed=seed, **kwargs)
    raw, defect_labels = inject_defects(truth, seed=seed)
    return {"truth": truth, "raw": raw,
            "anomaly_labels": anomaly_labels, "defect_labels": defect_labels}


def write_datasets(datasets):
    """Write the raw sales file and the two label files. The clean truth is NOT written."""
    config.DATA_RAW.mkdir(parents=True, exist_ok=True)
    datasets["raw"].to_csv(config.RAW_SALES_FILE, index=False, float_format="%.2f")
    datasets["anomaly_labels"].to_csv(config.ANOMALY_LABELS_FILE, index=False)
    datasets["defect_labels"].to_csv(config.DEFECT_LABELS_FILE, index=False)


def main():
    parser = argparse.ArgumentParser(description="Generate the synthetic retail dataset.")
    parser.add_argument("--seed", type=int, default=config.SEED, help="random seed (default: %(default)s)")
    args = parser.parse_args()

    data = build_datasets(seed=args.seed)
    write_datasets(data)
    truth, raw = data["truth"], data["raw"]
    print("SYNTHETIC DATA - not real company data")
    print(f"seed                    : {args.seed}")
    print(f"period                  : {config.START_DATE} to {config.END_DATE}")
    print(f"customers               : {truth['customer_id'].nunique()}")
    print(f"distinct orders (truth) : {truth['order_id'].nunique()}")
    print(f"order lines (truth)     : {len(truth)}")
    print(f"rows in raw file        : {len(raw)}  (includes planted defects and duplicates)")
    print(f"planted anomalies       : {len(data['anomaly_labels'])}  -> {config.ANOMALY_LABELS_FILE.name}")
    print(f"planted data defects    : {len(data['defect_labels'])}  -> {config.DEFECT_LABELS_FILE.name}")
    print(f"raw file written to     : {config.RAW_SALES_FILE}")


if __name__ == "__main__":
    main()
