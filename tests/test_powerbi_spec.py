"""Tests for the Power BI specification (src/powerbi_spec.py).

Power BI Desktop is not available in the test environment, so these tests check everything AROUND the DAX:
fields exist, relationships are valid on the real data, documents are up to date, and the star schema
(fact + dimensions, joined the way Power BI joins them) reproduces the KPIs computed by SQL/Python.
"""
import re

import pandas as pd
import pytest

from src import anomaly_detection as ad
from src import database, metrics, powerbi_export, powerbi_spec as spec
from src.filters import SalesFilter


# ----------------------------------------------------------------------------- real exported tables
@pytest.fixture(scope="module")
def tables(db_path, clean_df, tmp_path_factory):
    out = tmp_path_factory.mktemp("pbi_exports")
    connection = database.connect(db_path)
    try:
        powerbi_export.export_all(connection, out_dir=out)
    finally:
        connection.close()
    ad.export_weekly(ad.detect(clean_df), out / "anomaly_weekly.csv")
    return {name: pd.read_csv(out / f"{name}.csv") for name in spec.MODEL}


@pytest.fixture(scope="module")
def detections(clean_df):
    return ad.detect(clean_df)


# ----------------------------------------------------------------------------- the model matches the files
def test_every_model_table_and_column_exists_in_the_exports_with_the_same_column_order(tables):
    for name, definition in spec.MODEL.items():
        assert list(tables[name].columns) == list(definition["columns"]), name


def test_declared_types_are_plausible_for_the_data(tables):
    for name, definition in spec.MODEL.items():
        for column, declared in definition["columns"].items():
            series = tables[name][column].dropna()
            if declared in ("whole", "decimal") and len(series):
                assert pd.api.types.is_numeric_dtype(series), (name, column)
            if declared == "date" and len(series):
                assert pd.to_datetime(series, errors="coerce").notna().all(), (name, column)
            if declared == "true/false":
                assert set(series.astype(str)) <= {"True", "False"}, (name, column)


def test_relationships_are_valid_on_the_real_data(tables):
    for one, many, _ in spec.RELATIONSHIPS:
        (one_t, one_c), (many_t, many_c) = spec.TOKEN.findall(one)[0], spec.TOKEN.findall(many)[0]
        one_values, many_values = tables[one_t][one_c], tables[many_t][many_c]
        assert one_values.is_unique, f"{one} must be unique to be the one side"
        assert not one_values.isna().any()
        orphans = set(many_values.dropna().astype(str)) - set(one_values.astype(str))
        assert not orphans, f"{many} has values missing from {one}: {sorted(orphans)[:3]}"


def test_sort_by_columns_exist_and_are_one_to_one(tables):
    for label, order in spec.SORT_BY:
        (t, c), (t2, c2) = spec.TOKEN.findall(label)[0], spec.TOKEN.findall(order)[0]
        pairs = tables[t][[c, c2]].drop_duplicates()
        assert pairs[c].is_unique and pairs[c2].is_unique, (label, order)


def test_a_sales_region_always_equals_its_customers_region(clean_df, tables):
    """The spec takes region from dim_customer. That is only correct if the two agree."""
    merged = clean_df.merge(tables["dim_customer"], on="customer_id", suffixes=("", "_dim"))
    assert len(merged) == len(clean_df)
    assert (merged["region"] == merged["region_dim"]).all()
    assert (merged["customer_segment"] == merged["customer_segment_dim"]).all()


def test_the_date_table_covers_every_fact_date_without_gaps(tables):
    dates = pd.to_datetime(tables["dim_date"]["date"])
    assert (dates.diff().dropna() == pd.Timedelta(days=1)).all()
    facts = pd.to_datetime(tables["fact_sales"]["order_date"])
    assert dates.min() <= facts.min() and facts.max() <= dates.max()


# ----------------------------------------------------------------------------- DAX and visuals reference real things
def _check_references(text, where, tables):
    columns, measures = spec.references(text)
    for table, column in columns:
        assert table in tables and column in tables[table].columns, f"{where}: unknown field {table}[{column}]"
    for measure in measures:
        assert measure in spec.MEASURE_NAMES, f"{where}: unknown measure [{measure}]"


def test_every_dax_measure_references_only_existing_fields_and_measures(tables):
    for m in spec.MEASURES:
        _check_references(m["dax"], f"measure {m['name']}", tables)


def test_measure_names_are_unique_and_every_measure_is_documented(tables):
    assert len(spec.MEASURE_NAMES) == len(set(spec.MEASURE_NAMES))
    for m in spec.MEASURES:
        assert m["why"] and m["format"] and m["group"], m["name"]
    text = spec.render_dax_measures()
    for name in spec.MEASURE_NAMES:
        assert f"### {name}" in text


def test_measures_do_not_reference_themselves_or_form_cycles():
    deps = {m["name"]: spec.references(m["dax"])[1] for m in spec.MEASURES}

    def visit(name, path):
        assert name not in path, f"circular measure: {' -> '.join(path + [name])}"
        for child in deps[name]:
            visit(child, path + [name])
    for name in deps:
        visit(name, [])


def test_division_always_uses_divide_so_zero_denominators_cannot_error():
    for m in spec.MEASURES:
        assert " / " not in m["dax"], m["name"]


def test_change_measures_use_the_abs_rule_of_the_requirements():
    for name in ("Revenue MoM %", "Profit MoM %", "Revenue YoY %", "Profit YoY %"):
        dax = next(m["dax"] for m in spec.MEASURES if m["name"] == name)
        assert "ABS (" in dax and "DIVIDE" in dax, name


def test_distinct_counts_use_distinctcount_not_countrows():
    orders = next(m["dax"] for m in spec.MEASURES if m["name"] == "Distinct Orders")
    assert "DISTINCTCOUNT" in orders and "order_id" in orders


def test_every_visual_field_exists(tables):
    for page in spec.PAGES:
        for title, kind, fields, notes in page["visuals"]:
            for field in fields:
                _check_references(field, f"{page['name']} / {title}", tables)
        for slicer in page.get("extra_slicers", []):
            _check_references(slicer, f"{page['name']} slicer", tables)
    for slicer in spec.SLICERS.values():
        _check_references(slicer, "global slicer", tables)


def test_the_five_required_pages_and_slicers_are_present():
    assert spec.PAGE_NAMES == ["Executive Overview", "Product & Category Performance", "Customer Analysis",
                               "Regional Performance", "Anomaly Monitor"]            # FR-29
    for page in spec.PAGES[:4]:
        assert set(page["slicers"]) == {"Date", "Region", "Category", "Segment"}      # FR-30
    assert "Date" in spec.PAGES[4]["slicers"]


def test_every_page_has_a_stakeholder_questions_and_visuals():
    from src import kpi_catalogue
    for page in spec.PAGES:
        assert page["stakeholder"] in kpi_catalogue.STAKEHOLDERS
        assert page["questions"] and page["visuals"] and page["purpose"]


def test_distinct_orders_is_not_shown_with_a_total_in_the_category_matrix():
    product_page = spec.PAGES[1]
    assert any("not additive" in n.lower() for n in product_page["notes"])


# ----------------------------------------------------------------------------- the star schema reproduces the KPIs
def dax_replica(fact, dim_customer, dim_product, dim_date, start=None, end=None, **dim_filters):
    """Re-implements the measures with Power BI's filtering path: slicers filter DIMENSIONS, which filter the fact."""
    dates = dim_date
    if start:
        dates = dates[(dates["date"] >= start) & (dates["date"] <= end)]
    customers, products = dim_customer, dim_product
    if "region" in dim_filters:
        customers = customers[customers["region"] == dim_filters["region"]]
    if "customer_segment" in dim_filters:
        customers = customers[customers["customer_segment"] == dim_filters["customer_segment"]]
    if "category" in dim_filters:
        products = products[products["category"] == dim_filters["category"]]
    f = fact[fact["order_date"].isin(dates["date"]) & fact["customer_id"].isin(customers["customer_id"])
             & fact["product_id"].isin(products["product_id"])]
    revenue, profit = f["revenue"].sum(), f["profit"].sum()
    orders = f["order_id"].nunique()
    customers_n = f["customer_id"].nunique()
    per_customer = f.groupby("customer_id")["order_id"].nunique()
    repeat = (per_customer >= 2).sum()
    by_customer = f.groupby("customer_id")["revenue"].sum().sort_values(ascending=False)
    return {
        "revenue": revenue, "profit": profit, "profit_margin_pct": (profit / revenue * 100) if revenue else None,
        "distinct_orders": orders, "order_lines": len(f), "units_sold": f["quantity"].sum(),
        "avg_order_value": revenue / orders if orders else None,
        "active_customers": customers_n, "revenue_per_customer": revenue / customers_n if customers_n else None,
        "repeat_customer_rate": repeat / customers_n if customers_n else None,
        "top10_share_pct": by_customer.head(10).sum() / revenue * 100 if revenue else None,
    }


CASES = [
    {}, {"start": "2025-01-01", "end": "2025-12-31"}, {"region": "South"}, {"category": "Electronics"},
    {"customer_segment": "Corporate"},
    {"start": "2025-06-01", "end": "2025-08-31", "region": "West", "category": "Clothing"},
    {"start": "2030-01-01", "end": "2030-12-31"},                                   # nothing selected
]


@pytest.mark.parametrize("case", CASES, ids=lambda c: str(c) or "no filter")
def test_star_schema_reproduces_the_python_kpis_under_filters(clean_df, tables, case):
    fact = tables["fact_sales"]
    result = dax_replica(fact, tables["dim_customer"], tables["dim_product"], tables["dim_date"], **case)
    flt = SalesFilter(start_date=case.get("start"), end_date=case.get("end"), region=case.get("region"),
                      category=case.get("category"), customer_segment=case.get("customer_segment"))
    expected = metrics.compute_kpis(clean_df, flt)
    customers = metrics.customer_metrics(clean_df, flt)
    expected["top10_share_pct"] = metrics.top_n_customer_share(customers, 10)
    for key, value in result.items():
        if value is None or expected[key] is None:
            assert value is None and expected[key] is None, key
        else:
            assert value == pytest.approx(expected[key], rel=1e-9, abs=1e-6), key


def test_the_fact_table_has_no_region_so_the_spec_must_use_the_dimension(tables):
    assert "region" not in tables["fact_sales"].columns and "category" not in tables["fact_sales"].columns


def test_month_on_month_rule_matches_the_exported_answer_key(tables):
    monthly = tables["kpi_monthly"]
    revenue = monthly["revenue"]
    expected = (revenue - revenue.shift(1)) / revenue.shift(1).abs() * 100
    pd.testing.assert_series_equal(expected.round(4).iloc[1:], monthly["revenue_change_pct"].iloc[1:], check_names=False, atol=1e-3)


def test_anomaly_measures_match_the_exported_weekly_table(tables, detections):
    weekly = tables["anomaly_weekly"]
    flagged = weekly[weekly["is_anomaly"].astype(str) == "True"]
    assert len(flagged) == int(detections["is_anomaly"].sum())
    assert (weekly["status"] == "evaluated").sum() == int((detections["status"] == "evaluated").sum())
    assert flagged["deviation"].abs().max() == pytest.approx(detections.loc[detections["is_anomaly"], "deviation"].abs().max(), abs=1e-3)


# ----------------------------------------------------------------------------- documents
def test_documents_are_up_to_date_with_the_code(clean_df, detections):
    rendered = spec.render_all(clean_df, detections)
    for name, text in rendered.items():
        assert (spec.POWERBI_DIR / name).read_text(encoding="utf-8") == text, f"{name} is stale: run python -m src.powerbi_spec"


def test_documents_state_that_data_is_synthetic_and_the_dashboard_is_not_yet_validated(clean_df, detections):
    for name, text in spec.render_all(clean_df, detections).items():
        assert "synthetic" in text.lower(), name
        assert "Status: specification" in text, name
    assert "Build status" in spec.render_dashboard_spec()


def test_validation_numbers_in_the_guide_come_from_the_pipeline(clean_df, detections):
    guide = spec.render_build_guide(clean_df, detections)
    k = metrics.compute_kpis(clean_df)
    assert f"{k['revenue']:,.2f}" in guide and f"{k['distinct_orders']:,}" in guide
    assert f"{int(detections['is_anomaly'].sum()):,}" in guide


def test_the_guide_works_without_data_and_says_how_to_fill_it_in():
    assert "python run_pipeline.py" in spec.render_build_guide()


def test_write_all_creates_the_four_documents(tmp_path, clean_df, detections):
    names = spec.write_all(clean_df, detections, directory=tmp_path / "pbi")
    assert names == ["build_guide.md", "dashboard_spec.md", "data_model.md", "dax_measures.md"]
    assert all((tmp_path / "pbi" / n).read_text(encoding="utf-8").startswith("# ") for n in names)
