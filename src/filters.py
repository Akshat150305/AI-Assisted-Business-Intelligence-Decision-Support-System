"""One filter object shared by the SQL layer and the Python layer.

Using the same object for both is what makes "the same filters produce the same figures"
testable: SQL receives ``flt.to_params()`` and pandas receives ``flt.apply(df)``.

A filter field left as None means "no restriction". Dates are inclusive on both ends.
"""
from dataclasses import dataclass
from typing import Optional

import pandas as pd


def _iso(value, name):
    """Return 'YYYY-MM-DD' for a date-like value (str, date, Timestamp) or None."""
    if value is None:
        return None
    try:
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name} is not a valid date: {value!r}") from exc


@dataclass(frozen=True)
class SalesFilter:
    start_date: Optional[str] = None        # inclusive
    end_date: Optional[str] = None          # inclusive
    region: Optional[str] = None
    category: Optional[str] = None
    customer_segment: Optional[str] = None

    def __post_init__(self):
        # Normalise dates once so SQL (text comparison) and pandas (datetime comparison) agree.
        object.__setattr__(self, "start_date", _iso(self.start_date, "start_date"))
        object.__setattr__(self, "end_date", _iso(self.end_date, "end_date"))
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError(f"start_date {self.start_date} is after end_date {self.end_date}")

    @property
    def has_dates(self):
        return self.start_date is not None or self.end_date is not None

    def to_params(self):
        """Named parameters for the SQL files in sql/ (None = no restriction)."""
        return {
            "start_date": self.start_date,
            "end_date": self.end_date,
            "region": self.region,
            "category": self.category,
            "segment": self.customer_segment,
        }

    def apply(self, df):
        """Return the rows of a sales DataFrame (with a datetime ``order_date``) that match."""
        mask = pd.Series(True, index=df.index)
        if self.start_date is not None:
            mask &= df["order_date"] >= pd.Timestamp(self.start_date)
        if self.end_date is not None:
            mask &= df["order_date"] <= pd.Timestamp(self.end_date)
        if self.region is not None:
            mask &= df["region"] == self.region
        if self.category is not None:
            mask &= df["category"] == self.category
        if self.customer_segment is not None:
            mask &= df["customer_segment"] == self.customer_segment
        return df[mask]


NO_FILTER = SalesFilter()
