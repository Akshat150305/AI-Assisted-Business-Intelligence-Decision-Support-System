import importlib

import pytest

from app_config import config
from src import kpi_catalogue as cat
from src import metrics


def test_every_kpi_calculated_in_metrics_is_documented():
    documented = {k["key"] for k in cat.KPIS if k["key"]}
    assert set(metrics.KPI_NAMES) <= documented, set(metrics.KPI_NAMES) - documented


def test_every_entry_has_all_required_fields_filled_in():
    for kpi in cat.KPIS:
        for field in cat.REQUIRED_FIELDS:
            assert kpi.get(field), f"{kpi['name']}: '{field}' is empty"


def test_stakeholders_are_the_five_defined_in_the_requirements():
    for kpi in cat.KPIS:
        assert set(kpi["stakeholders"]) <= set(cat.STAKEHOLDERS), kpi["name"]
    assert len(cat.STAKEHOLDERS) == 5


def test_names_are_unique():
    names = [k["name"] for k in cat.KPIS]
    assert len(names) == len(set(names))


def test_every_referenced_sql_file_and_python_function_exists():
    for kpi in cat.KPIS:
        if kpi["sql_file"]:
            assert (config.SQL_DIR / f"{kpi['sql_file']}.sql").exists(), kpi["name"]
        module_name, function_name = kpi["python_function"].rsplit(".", 1)
        assert callable(getattr(importlib.import_module(module_name), function_name)), kpi["python_function"]


def test_the_kpis_named_in_the_requirements_are_present():
    names = " ".join(k["name"].lower() for k in cat.KPIS)
    for required in ("revenue", "profit", "profit margin", "distinct orders", "average order value",
                     "period-over-period", "anomaly deviation", "top-n customer share"):
        assert required in names, required


def test_zero_denominator_rule_is_stated_for_every_ratio():
    for kpi in cat.KPIS:
        if kpi["key"] in ("profit_margin_pct", "avg_order_value", "revenue_per_customer"):
            assert "not calculated" in kpi["formula"].lower(), kpi["name"]


def test_catalogue_quotes_the_parameters_actually_in_use():
    anomaly = next(k for k in cat.KPIS if k["name"].startswith("Anomaly"))
    assert str(config.ANOMALY_WINDOW_WEEKS) in anomaly["formula"]
    assert f"{config.ANOMALY_THRESHOLD:g}" in anomaly["formula"]
    assert "not a confirmed business issue" in anomaly["caveats"].lower()


def test_repeat_rate_is_not_presented_as_churn():
    repeat = next(k for k in cat.KPIS if k["key"] == "repeat_customer_rate")
    assert "NOT a churn" in repeat["caveats"]


def test_the_committed_document_is_up_to_date_with_the_code():
    """If this fails, run: python -m src.kpi_catalogue"""
    assert cat.CATALOGUE_FILE.read_text(encoding="utf-8") == cat.render_markdown()


def test_document_explains_measure_vs_calculated_column_vs_power_query():
    text = cat.render_markdown()
    for term in ("Power Query transformation", "Calculated column", "Measure (DAX)"):
        assert term in text


def test_write_catalogue_creates_the_file(tmp_path):
    path = cat.write_catalogue(tmp_path / "sub" / "kpis.md")
    assert path.exists() and path.read_text(encoding="utf-8").startswith("# KPI Catalogue")
