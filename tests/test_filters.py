import pandas as pd
import pytest

from src.filters import NO_FILTER, SalesFilter


def test_default_filter_restricts_nothing(tiny_df):
    assert len(NO_FILTER.apply(tiny_df)) == len(tiny_df)
    assert NO_FILTER.to_params() == {"start_date": None, "end_date": None, "region": None,
                                     "category": None, "segment": None}


def test_dates_are_normalised_to_iso_text():
    flt = SalesFilter(start_date=pd.Timestamp("2025-01-05"), end_date="2025/01/20")
    assert flt.start_date == "2025-01-05" and flt.end_date == "2025-01-20"


def test_invalid_date_raises():
    with pytest.raises(ValueError, match="not a valid date"):
        SalesFilter(start_date="not-a-date")


def test_start_after_end_raises():
    with pytest.raises(ValueError, match="after end_date"):
        SalesFilter(start_date="2025-02-01", end_date="2025-01-01")


def test_date_bounds_are_inclusive_on_both_ends(tiny_df):
    rows = SalesFilter(start_date="2025-01-10", end_date="2025-01-20").apply(tiny_df)
    assert sorted(rows["order_id"].unique()) == ["A", "B"]
    one_day = SalesFilter(start_date="2025-01-20", end_date="2025-01-20").apply(tiny_df)
    assert one_day["order_id"].tolist() == ["B"]


def test_each_attribute_filter_and_their_combination(tiny_df):
    assert set(SalesFilter(region="South").apply(tiny_df)["order_id"]) == {"C"}
    assert set(SalesFilter(category="Clothing").apply(tiny_df)["order_id"]) == {"A", "C"}
    assert set(SalesFilter(customer_segment="Corporate").apply(tiny_df)["order_id"]) == {"C"}
    both = SalesFilter(region="North", category="Clothing").apply(tiny_df)
    assert len(both) == 1 and both["order_id"].iloc[0] == "A"


def test_unknown_value_gives_empty_selection_not_an_error(tiny_df):
    assert SalesFilter(region="Mars").apply(tiny_df).empty


def test_filter_is_immutable():
    with pytest.raises(Exception):
        NO_FILTER.region = "North"
