"""Tests for the cleaning pipeline, run on the full synthetic raw data."""
import pandas as pd
import pytest

from app_config import config
from src import data_pipeline, validation


# ------------------------------------------------------------------------- reconciliation
def test_every_raw_row_is_either_clean_or_rejected(cleaning_result):
    r = cleaning_result
    assert r.reconciles
    assert r.n_raw == len(r.clean) + len(r.rejected)


def test_clean_and_rejected_rows_do_not_overlap(cleaning_result):
    r = cleaning_result
    assert r.rejected["source_row"].is_unique
    # Clean rows keep their order_id/line_number key; a rejected duplicate shares a key with a kept row,
    # so overlap is checked on raw row numbers via the audit log instead.
    removed_rows = set(r.audit.query("action == 'REMOVED'")["source_row"])
    assert removed_rows == set(r.rejected["source_row"])
    assert len(r.clean) == r.n_raw - len(removed_rows)


def test_clean_table_passes_every_validation_rule(cleaning_result):
    assert cleaning_result.final_issues.empty
    assert validation.validate_clean(cleaning_result.clean).empty


def test_clean_table_has_correct_columns_and_types(cleaning_result):
    c = cleaning_result.clean
    assert list(c.columns) == config.SALES_COLUMNS
    assert not c.isna().any().any()
    assert pd.api.types.is_datetime64_any_dtype(c["order_date"])
    assert c["quantity"].dtype.kind == "i" and c["line_number"].dtype.kind == "i"
    assert not c.duplicated(["order_id", "line_number"]).any()


def test_money_columns_are_consistent_after_cleaning(cleaning_result):
    c = cleaning_result.clean
    assert (c["revenue"] - (c["quantity"] * c["unit_price"]).round(2)).abs().max() <= config.MONEY_TOLERANCE
    assert (c["profit"] - (c["revenue"] - c["cost"]).round(2)).abs().max() <= config.MONEY_TOLERANCE


def test_distinct_orders_are_fewer_than_order_lines(cleaning_result):
    c = cleaning_result.clean
    assert c["order_id"].nunique() < len(c)


# ------------------------------------------------------------------------- audit trail
def test_audit_log_is_complete_and_well_formed(cleaning_result):
    a = cleaning_result.audit
    assert list(a.columns) == data_pipeline.AUDIT_COLUMNS
    assert a["log_id"].is_unique
    assert set(a["action"]) <= {"REPAIRED", "REMOVED"}
    assert a["reason"].notna().all() and (a["reason"].str.len() > 0).all()
    assert set(a["rule_id"]) <= set(validation.RULES)


def test_every_rejected_row_has_a_logged_reason(cleaning_result):
    r = cleaning_result
    logged_removals = set(r.audit.query("action == 'REMOVED'")["source_row"])
    assert set(r.rejected["source_row"]) == logged_removals
    assert r.rejected["rejection_reasons"].notna().all()
    assert r.rejected["rejection_rules"].notna().all()


def test_repairs_record_old_and_new_values(cleaning_result):
    repairs = cleaning_result.audit.query("action == 'REPAIRED'")
    assert len(repairs) > 0
    assert repairs["column"].notna().all() and (repairs["column"] != "").all()
    assert repairs["new_value"].notna().all()


# ------------------------------------------------------------------------- planted defects (labels used only here)
def test_every_planted_defect_is_handled_as_expected(cleaning_result, datasets):
    report = data_pipeline.evaluate_against_defect_labels(cleaning_result.audit, datasets["defect_labels"])
    missed = report[report["handled_as_expected"] != report["planted"]]
    assert missed.empty, f"planted defects not handled as expected:\n{missed}"


def test_cleaning_does_not_read_the_defect_labels():
    import inspect
    source = inspect.getsource(data_pipeline.clean_sales) + inspect.getsource(data_pipeline.load_raw_sales)
    assert "DEFECT_LABELS_FILE" not in source and "defect_labels" not in source


def test_cleaning_recovers_the_true_data_for_repaired_rows(cleaning_result, datasets):
    """Repaired rows should end up equal to the original truth (the strongest correctness check)."""
    truth = datasets["truth"].copy()
    clean = cleaning_result.clean.copy()
    merged = clean.merge(truth, on=["order_id", "line_number"], suffixes=("", "_true"))
    for col in ["customer_segment", "category", "region", "product_name", "revenue", "profit", "unit_price"]:
        if col in ("revenue", "profit", "unit_price"):
            diff = (merged[col] - merged[f"{col}_true"]).abs()
            assert diff.max() <= 0.011, f"{col} differs from the truth by up to {diff.max()}"
        else:
            assert (merged[col] == merged[f"{col}_true"]).all(), f"{col} differs from the truth"


def test_only_planted_rows_are_rejected(cleaning_result, datasets):
    """Nothing valid is thrown away: every rejected row is one the generator broke on purpose."""
    labels = datasets["defect_labels"]
    removed_by_design = set(labels.query("expected_action == 'REMOVED'")["source_row"])
    assert set(cleaning_result.rejected["source_row"]) == removed_by_design


# ------------------------------------------------------------------------- edge cases
def test_cleaning_an_empty_file_does_not_crash(tmp_path):
    path = tmp_path / "empty.csv"
    pd.DataFrame(columns=config.SALES_COLUMNS).to_csv(path, index=False)
    result = data_pipeline.clean_sales(data_pipeline.load_raw_sales(path))
    assert len(result.clean) == 0 and len(result.rejected) == 0 and result.reconciles


def test_loading_a_file_with_missing_columns_raises(tmp_path):
    path = tmp_path / "bad.csv"
    pd.DataFrame({"order_id": ["A"]}).to_csv(path, index=False)
    with pytest.raises(ValueError, match="missing columns"):
        data_pipeline.load_raw_sales(path)


def test_load_raw_sales_keeps_values_as_text(raw_csv_path):
    raw = data_pipeline.load_raw_sales(raw_csv_path)
    assert raw["source_row"].iloc[0] == 1
    assert all(raw[c].dtype == object or str(raw[c].dtype).startswith("str") for c in config.SALES_COLUMNS)


def test_quality_summary_uses_real_numbers_and_mentions_synthetic_data(cleaning_result):
    text = data_pipeline.build_quality_summary(cleaning_result)
    assert "Synthetic data" in text
    assert f"{cleaning_result.n_raw:,}" in text
    assert f"{len(cleaning_result.clean):,}" in text
    assert "Yes" in text.split("Raw = clean + rejected?")[1].splitlines()[0]


def test_write_outputs_creates_all_files(cleaning_result, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_PROCESSED", tmp_path / "processed")
    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(config, "CLEAN_SALES_FILE", tmp_path / "processed" / "sales_clean.csv")
    monkeypatch.setattr(config, "REJECTED_ROWS_FILE", tmp_path / "processed" / "rejected_rows.csv")
    monkeypatch.setattr(config, "AUDIT_LOG_FILE", tmp_path / "processed" / "cleaning_audit_log.csv")
    monkeypatch.setattr(config, "QUALITY_SUMMARY_FILE", tmp_path / "reports" / "data_quality_summary.md")
    data_pipeline.write_outputs(cleaning_result)
    reloaded = pd.read_csv(config.CLEAN_SALES_FILE)
    assert len(reloaded) == len(cleaning_result.clean)
    assert round(reloaded["revenue"].sum(), 2) == round(cleaning_result.clean["revenue"].sum(), 2)
    assert config.QUALITY_SUMMARY_FILE.exists()
