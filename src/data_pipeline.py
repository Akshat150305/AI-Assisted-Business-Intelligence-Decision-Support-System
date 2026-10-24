"""Cleaning pipeline: raw sales -> clean sales + audit log + rejected rows.

Principles
----------
* Nothing is silently deleted or changed. Every repair and every removal is written to the
  audit log (data/processed/cleaning_audit_log.csv) with the rule, old value, new value and reason.
* Removed rows are not thrown away. They are quarantined in data/processed/rejected_rows.csv.
* Repairs are only made when the correct value can be derived from other data (for example
  revenue = quantity x unit_price, or a product's category from the product's other rows).
  Anything that cannot be derived is quarantined.
* The planted-defect label file is NEVER read during cleaning. It is only used afterwards,
  by ``evaluate_against_defect_labels``, to measure how well the pipeline did.

Run from the project root:   python -m src.data_pipeline
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app_config import config
from src import validation as v

REQUIRED_COLUMNS = [
    "order_id", "line_number", "order_date", "customer_id", "product_id", "product_name",
    "category", "region", "customer_segment", "quantity", "unit_price", "cost",
]
AUDIT_COLUMNS = ["log_id", "source_row", "order_id", "line_number", "rule_id", "rule_name",
                 "action", "column", "old_value", "new_value", "reason"]


# --------------------------------------------------------------------------- loading
def load_raw_sales(path=config.RAW_SALES_FILE):
    """Read the raw file as TEXT so nothing is altered before validation.

    Only a truly empty cell becomes missing (NaN). Text such as "N/A" is kept as text so that
    it can be reported as an invalid value. ``source_row`` is the 1-based row number in the file.
    """
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
    missing = [c for c in config.SALES_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Raw sales file is missing columns: {missing}")
    df = df[config.SALES_COLUMNS].copy()
    df.insert(0, "source_row", np.arange(1, len(df) + 1))
    return df


# --------------------------------------------------------------------------- audit log helper
class _Audit:
    """Collects audit-log entries. ``raw`` is the untouched raw table indexed by source_row."""

    def __init__(self, raw):
        self.raw = raw
        self.frames = []

    def add(self, rows, rule_id, reason, column="", new=None, action="REPAIRED"):
        idx = rows.index[rows] if isinstance(rows, pd.Series) else pd.Index(rows)
        if len(idx) == 0:
            return
        n = len(idx)
        old = self.raw.loc[idx, column].to_numpy() if column else np.full(n, np.nan, dtype=object)
        if new is None:
            new_values = np.full(n, np.nan, dtype=object)
        elif isinstance(new, pd.Series):
            new_values = new.loc[idx].to_numpy()
        else:
            new_values = np.full(n, new, dtype=object)
        self.frames.append(pd.DataFrame({
            "source_row": idx.to_numpy(),
            "order_id": self.raw.loc[idx, "order_id"].to_numpy(),
            "line_number": self.raw.loc[idx, "line_number"].to_numpy(),
            "rule_id": rule_id,
            "rule_name": v.RULES[rule_id]["name"],
            "action": action,
            "column": column,
            "old_value": old,
            "new_value": new_values,
            "reason": reason,
        }))

    def finalize(self):
        if not self.frames:
            return pd.DataFrame(columns=AUDIT_COLUMNS)
        log = pd.concat(self.frames, ignore_index=True)
        log = log.sort_values(["source_row", "rule_id", "column"], kind="stable").reset_index(drop=True)
        log.insert(0, "log_id", [f"L{i:06d}" for i in range(1, len(log) + 1)])
        return log[AUDIT_COLUMNS]


def _build_lookup(work, active, key, value_col, valid):
    """Most common valid value of ``value_col`` for each ``key`` (ties broken alphabetically)."""
    sel = active & work[key].notna() & valid
    counts = work[sel].groupby([key, value_col]).size().rename("n").reset_index()
    return (counts.sort_values([key, "n", value_col], ascending=[True, False, True])
            .drop_duplicates(key).set_index(key)[value_col])


# --------------------------------------------------------------------------- result container
@dataclass
class CleaningResult:
    clean: pd.DataFrame
    rejected: pd.DataFrame
    audit: pd.DataFrame
    final_issues: pd.DataFrame      # validation problems left in the clean table (must be empty)
    n_raw: int

    @property
    def reconciles(self):
        """Every raw row is either in the clean table or in the rejected table."""
        return self.n_raw == len(self.clean) + len(self.rejected)


# --------------------------------------------------------------------------- cleaning
def clean_sales(raw):
    """Clean the raw table (as produced by ``load_raw_sales``) and return a CleaningResult."""
    raw_str = raw.set_index("source_row")
    work = raw_str.copy()
    audit = _Audit(raw_str)
    removed = pd.Series(False, index=work.index)
    pending = pd.Series(False, index=work.index)

    def remove(mask, rule_id, reason, column=""):
        """Log a removal. A row may fail several rules in one stage, so all reasons are kept."""
        nonlocal pending
        mask = mask & ~removed
        audit.add(mask, rule_id, reason, column, action="REMOVED")
        pending = pending | mask

    def commit():
        nonlocal removed, pending
        removed = removed | pending
        pending = pd.Series(False, index=work.index)

    # ---- Stage 1: duplicates (checked on the raw values, before any repair)
    remove(v.exact_duplicate(work, config.SALES_COLUMNS), "R01",
           "identical to an earlier row; first copy kept")
    commit()
    remove(v.duplicate_key(work), "R02",
           "same order_id and line_number as an earlier row but different content; first occurrence kept")
    commit()

    # ---- Stage 2: standardise text (trim spaces, fix upper/lower case)
    active = ~removed
    for col in config.TEXT_COLUMNS:
        original = work[col]
        cleaned = original.str.strip()
        cleaned = cleaned.where(cleaned != "", np.nan)
        if col in v.ALLOWED:
            recognised = cleaned.str.lower().map(v.canonical_map(v.ALLOWED[col]))
            cleaned = recognised.where(recognised.notna(), cleaned)
        changed = active & original.notna() & (cleaned.fillna("\x00") != original)
        audit.add(changed, "R11", f"{col}: spaces or letter case standardised",
                  column=col, new=cleaned, action="REPAIRED")
        work[col] = cleaned

    # ---- Stage 3: convert dates and numbers (values that cannot be converted are remembered)
    date_text = work["order_date"]
    dates = v.parse_dates(date_text)
    bad_format = date_text.notna() & dates.isna()
    out_of_range = v.date_out_of_range(dates)
    work["order_date"] = dates
    not_numeric = {"order_date": bad_format}
    for col in config.NUMERIC_COLUMNS:
        parsed = v.parse_numbers(work[col])
        not_numeric[col] = work[col].notna() & parsed.isna()
        work[col] = parsed

    # ---- Stage 4: repair values that can be rebuilt from other rows
    active = ~removed
    for col, key in [("customer_segment", "customer_id"), ("region", "customer_id"),
                     ("category", "product_id"), ("product_name", "product_id")]:
        allowed = v.ALLOWED.get(col)
        valid = work[col].isin(allowed) if allowed else work[col].notna()
        lookup = _build_lookup(work, active, key, col, valid)
        replacement = work[key].map(lookup)
        invalid = v.not_allowed(work[col], allowed) if allowed else pd.Series(False, index=work.index)
        for kind, problem, rule in (("blank", work[col].isna(), "R13"), ("not an allowed value", invalid, "R12")):
            fixable = active & problem & replacement.notna()
            audit.add(fixable, rule, f"{col} is {kind}; taken from the other rows of this {key}",
                      column=col, new=replacement, action="REPAIRED")
            work.loc[fixable, col] = replacement[fixable]

    price_blank = work["unit_price"].isna() & ~not_numeric["unit_price"]
    can_derive = (active & price_blank & work["revenue"].notna() & (work["revenue"] > 0)
                  & work["quantity"].notna() & (work["quantity"] > 0))
    derived_price = (work["revenue"] / work["quantity"].where(work["quantity"] > 0)).round(2)
    audit.add(can_derive, "R13", "unit_price is blank; rebuilt as revenue / quantity",
              column="unit_price", new=derived_price, action="REPAIRED")
    work.loc[can_derive, "unit_price"] = derived_price[can_derive]

    # ---- Stage 5: quarantine rows with problems that cannot be repaired
    for col in REQUIRED_COLUMNS:
        blank = work[col].isna() & ~not_numeric.get(col, pd.Series(False, index=work.index))
        remove(blank, "R03", f"required field '{col}' is blank and cannot be repaired", column=col)
    remove(bad_format, "R04", "order_date is not a valid YYYY-MM-DD date", "order_date")
    remove(out_of_range, "R04",
           f"order_date is outside the allowed period {config.START_DATE} to {config.END_DATE}", "order_date")
    remove(v.invalid_quantity(work["quantity"]) | not_numeric["quantity"],
           "R05", "quantity is not a positive whole number", "quantity")
    remove(v.invalid_price(work["unit_price"]) | not_numeric["unit_price"],
           "R06", "unit_price is zero, negative or not a number", "unit_price")
    remove(v.invalid_cost(work["cost"]) | not_numeric["cost"],
           "R14", "cost is negative or not a number", "cost")
    for col, allowed in v.ALLOWED.items():
        remove(v.not_allowed(work[col], allowed), "R12",
               f"{col} is not an allowed value and no reference value exists", col)
    outlier = v.price_outlier(work[~removed]).reindex(work.index, fill_value=False)
    remove(outlier, "R08",
           f"unit_price is outside {config.PRICE_BAND[0]}x to {config.PRICE_BAND[1]}x of the product's median price",
           "unit_price")
    commit()

    # ---- Stage 6: orders whose lines disagree about customer, date or region
    conflict = v.header_conflict(work[~removed]).reindex(work.index, fill_value=False)
    remove(conflict, "R09", "customer, date or region disagrees with the rest of the order", "order_id")
    commit()

    # ---- Stage 7: recalculate derived money columns where they are wrong or blank
    active = ~removed
    expected_revenue = (work["quantity"] * work["unit_price"]).round(2)
    bad_revenue = active & (work["revenue"].isna() | v.revenue_mismatch(work))
    audit.add(bad_revenue, "R07", "revenue recalculated as quantity x unit_price",
              column="revenue", new=expected_revenue, action="REPAIRED")
    work.loc[bad_revenue, "revenue"] = expected_revenue[bad_revenue]

    expected_profit = (work["revenue"] - work["cost"]).round(2)
    bad_profit = active & (work["profit"].isna() | v.profit_mismatch(work))
    audit.add(bad_profit, "R10", "profit recalculated as revenue - cost",
              column="profit", new=expected_profit, action="REPAIRED")
    work.loc[bad_profit, "profit"] = expected_profit[bad_profit]

    # ---- Stage 8: assemble outputs
    clean = work.loc[~removed, config.SALES_COLUMNS].copy()
    clean["line_number"] = clean["line_number"].astype(int)
    clean["quantity"] = clean["quantity"].astype(int)
    clean = clean.sort_values(["order_date", "order_id", "line_number"], kind="stable").reset_index(drop=True)

    audit_log = audit.finalize()
    removals = audit_log[audit_log["action"] == "REMOVED"]
    per_row = removals.groupby("source_row").agg(
        rejection_rules=("rule_id", lambda s: ";".join(sorted(set(s)))),
        rejection_reasons=("reason", lambda s: " | ".join(dict.fromkeys(s))),
    )
    rejected = raw_str.loc[removed].join(per_row).reset_index()

    return CleaningResult(clean=clean, rejected=rejected, audit=audit_log,
                          final_issues=v.validate_clean(clean), n_raw=len(raw))


# --------------------------------------------------------------------------- evaluation (labels used AFTER cleaning)
def evaluate_against_defect_labels(audit, labels):
    """Compare what the pipeline did with the planted defects. For evaluation only.

    A planted defect counts as handled when the audit log has an entry for the same source row
    with the expected rule and expected action.
    Returns a table: defect_type, planted, handled_as_expected.
    """
    seen = audit[["source_row", "rule_id", "action"]].drop_duplicates()
    merged = labels.merge(seen, how="left", left_on=["source_row", "expected_rule", "expected_action"],
                          right_on=["source_row", "rule_id", "action"], indicator=True)
    handled = (merged["_merge"] == "both").to_numpy()
    out = (labels.assign(handled=handled).groupby("defect_type")
           .agg(planted=("defect_id", "size"), handled_as_expected=("handled", "sum")).reset_index())
    return out


# --------------------------------------------------------------------------- reporting
def build_quality_summary(result):
    """Markdown data-quality summary built from the actual pipeline output (no hardcoded numbers)."""
    a = result.audit
    repairs = a[a["action"] == "REPAIRED"]
    removals = a[a["action"] == "REMOVED"]
    dup_rows = int(removals[removals["rule_id"].isin(["R01", "R02"])]["source_row"].nunique())
    lines = [
        "# Data Quality Summary",
        "",
        "> **Synthetic data.** Generated by `src/data_generator.py` from a fixed random seed. "
        "It does not represent a real company.",
        "> This file is generated by `src/data_pipeline.py`. All numbers come from the pipeline run.",
        "",
        "## Row reconciliation",
        "",
        "| Item | Rows |",
        "|---|---:|",
        f"| Rows in raw file | {result.n_raw:,} |",
        f"| Rows in clean table | {len(result.clean):,} |",
        f"| Rows quarantined (rejected) | {len(result.rejected):,} |",
        f"| - of which duplicates (R01, R02) | {dup_rows:,} |",
        f"| Rows with at least one repair | {repairs['source_row'].nunique():,} |",
        f"| Individual repairs logged | {len(repairs):,} |",
        f"| Raw = clean + rejected? | {'Yes' if result.reconciles else 'NO - investigate'} |",
        f"| Validation problems left in clean table | {int(result.final_issues['rows_affected'].sum()) if len(result.final_issues) else 0} |",
        "",
        "## What each rule did",
        "",
        "| Rule | Name | Action | Rows affected | Cleaning decision |",
        "|---|---|---|---:|---|",
    ]
    for rule_id, rule in v.RULES.items():
        n = int(a[a["rule_id"] == rule_id]["source_row"].nunique())
        lines.append(f"| {rule_id} | {rule['name']} | {rule['action']} | {n:,} | {rule['decision']} |")
    lines += [
        "",
        "A rule can fire on both repaired and removed rows (R12 repairs a row if a reference value exists, "
        "otherwise the row is removed). A row can break more than one rule.",
        "",
        "## Principles",
        "",
        "1. Nothing is silently deleted or changed. Every action is in `data/processed/cleaning_audit_log.csv`.",
        "2. Removed rows are kept in `data/processed/rejected_rows.csv` with the reasons.",
        "3. A value is only repaired when it can be derived from other data. Otherwise the row is quarantined.",
        "4. Duplicates: the first occurrence is kept. The first copy is assumed correct, which should be "
        "confirmed with the data source in a real project.",
        "",
        "## Limits of this summary",
        "",
        "- The pipeline can only detect problems that break a rule. A wrong value that still looks valid "
        "(for example a plausible but incorrect quantity) would pass undetected.",
        "- Quarantining rows removes their revenue from the analysis. In a real project the size of the "
        "rejected revenue would be reported to the data owner.",
    ]
    return "\n".join(lines) + "\n"


def write_outputs(result):
    config.DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    result.clean.to_csv(config.CLEAN_SALES_FILE, index=False, float_format="%.2f", date_format="%Y-%m-%d")
    result.rejected.to_csv(config.REJECTED_ROWS_FILE, index=False)
    result.audit.to_csv(config.AUDIT_LOG_FILE, index=False)
    config.QUALITY_SUMMARY_FILE.write_text(build_quality_summary(result), encoding="utf-8")


def run_cleaning():
    """Load the raw file, clean it, write every output. Raises if the clean table is not clean."""
    result = clean_sales(load_raw_sales())
    if len(result.final_issues):
        raise RuntimeError(f"Clean table still has validation problems:\n{result.final_issues}")
    if not result.reconciles:
        raise RuntimeError("Row counts do not reconcile: raw != clean + rejected")
    write_outputs(result)
    return result


def main():
    result = run_cleaning()
    print("SYNTHETIC DATA - not real company data")
    print(f"raw rows        : {result.n_raw:,}")
    print(f"clean rows      : {len(result.clean):,}")
    print(f"rejected rows   : {len(result.rejected):,}  (quarantined, see {config.REJECTED_ROWS_FILE.name})")
    print(f"audit entries   : {len(result.audit):,}  (see {config.AUDIT_LOG_FILE.name})")
    print(f"reconciles      : {result.reconciles}")
    print(f"quality summary : {config.QUALITY_SUMMARY_FILE}")
    if config.DEFECT_LABELS_FILE.exists():
        labels = pd.read_csv(config.DEFECT_LABELS_FILE)
        report = evaluate_against_defect_labels(result.audit, labels)
        print("\nEVALUATION ONLY - planted defects vs what the pipeline did (labels were not used for cleaning):")
        print(report.to_string(index=False))
        print(f"\nhandled as expected: {int(report['handled_as_expected'].sum())} of {int(report['planted'].sum())}")


if __name__ == "__main__":
    main()
