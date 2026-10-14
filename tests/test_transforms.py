"""
Transform steps (Power Query-style) and the wrong-data / wrong-format checks
(api/services/transforms.py), run through clean_dataframe like the app does.
"""

import pandas as pd
import pytest

from api.services.cleaning import clean_dataframe
from api.services.transforms import format_check, outlier_check


def _workouts() -> pd.DataFrame:
    # Like the W3Schools example: one Duration of 450 is clearly a typo for 45.
    return pd.DataFrame({
        "Duration": [60, 60, 60, 45, 45, 60, 60, 450, 30, 60, 60, 60, 45, 60],
        "Date": ["2020/12/01", "2020/12/02", "2020/12/03", "2020/12/04", "2020/12/05", "2020/12/06",
                 "2020/12/07", "2020/12/08", "2020/12/09", "2020/12/10", "2020/12/11", "2020/12/12",
                 "20201226", "not a date"],
        "Calories": [409.1, 479.0, 340.0, 282.4, 406.0, 300.0, 374.0, 253.3, 195.1, 269.0, 329.3, 250.7, 345.3, 379.3],
    })


def test_wrong_data_is_found_but_not_changed():
    cleaned, report = clean_dataframe(_workouts())
    dur = next(c for c in report.column_reports if c.column == "duration")
    assert dur.outliers["count"] == 1 and dur.outliers["examples"] == ["450"]
    assert (dur.outliers["low"], dur.outliers["high"]) == (15, 94)   # Q1 − 3×IQR, Q3 + 3×IQR (93.75 rounded)
    assert "duration" in report.outlier_columns
    assert cleaned["duration"].max() == 450          # only reported, never changed on its own


def test_wrong_format_is_found_with_the_values_that_wont_convert():
    _, report = clean_dataframe(_workouts())
    date = next(c for c in report.column_reports if c.column == "date")
    assert date.format_issue["to"] == "date"
    assert date.format_issue["failing"] == 1 and date.format_issue["examples"] == ["not a date"]
    assert format_check(pd.Series(["$1,200", "15", "7.5", "n/a", "8"]))["to"] == "number"
    assert format_check(pd.Series(["apple", "pear"])) is None


@pytest.mark.parametrize("method, expected_max, rows", [("cap", 120, 14), ("remove", 60, 13), ("blank", 60, 14)])
def test_rules_for_wrong_data(method, expected_max, rows):
    # W3Schools: "if Duration > 120, set it to 120" (cap), or drop those rows (remove).
    cleaned, report = clean_dataframe(_workouts(), transforms=[
        {"op": "outliers", "column": "duration", "method": method, "high": 120}])
    assert cleaned["duration"].max() == expected_max and len(cleaned) == rows
    assert report.transforms_applied[0]["count"] == 1
    assert report.outlier_columns == []              # the client's rule wins; not flagged again
    if method == "blank":
        assert cleaned["duration"].isna().sum() == 1


def test_replace_one_wrong_value_and_change_type_then_drop_blank_dates():
    cleaned, report = clean_dataframe(
        _workouts(),
        transforms=[{"op": "replace_values", "column": "duration", "find": "450", "replace": "45"},
                    {"op": "change_type", "column": "date", "to": "date"}],
        column_actions={"date": "drop_rows"},          # W3Schools: df.dropna(subset=['Date'])
    )
    assert cleaned["duration"].max() == 60
    assert pd.api.types.is_datetime64_any_dtype(cleaned["date"])
    assert len(cleaned) == 13                          # "not a date" became blank, then its row was removed
    assert "couldn't be converted" in report.transforms_applied[1]["text"]


def test_promote_headers_and_remove_top_rows():
    # An export with a title row, then the real header row, then data.
    raw = pd.DataFrame([["Sales report", None, None], ["Store", "Month", "Sales"],
                        ["North", "Jan", "10"], ["South", "Jan", "12"]], columns=["a", "b", "c"])
    cleaned, report = clean_dataframe(raw, transforms=[{"op": "remove_top_rows", "count": 1},
                                                       {"op": "promote_headers"}])
    assert list(cleaned.columns) == ["store", "month", "sales"]
    assert cleaned["sales"].tolist() == [10, 12]       # numbers again once the header row is gone
    assert [s["op"] for s in report.transforms_applied] == ["remove_top_rows", "promote_headers"]


def test_unpivot_and_pivot():
    wide = pd.DataFrame({"Store": ["North", "South"], "Jan": [10, 12], "Feb": [11, 13]})
    long, _ = clean_dataframe(wide, transforms=[
        {"op": "unpivot", "columns": ["jan", "feb"], "attribute": "Month", "value": "Sales"}])
    assert list(long.columns) == ["store", "month", "sales"] and len(long) == 4
    back, _ = clean_dataframe(long, transforms=[{"op": "pivot", "column": "month", "values": "sales", "agg": "sum"}])
    assert sorted(back.columns) == ["feb", "jan", "store"] and back["jan"].sum() == 22


def test_bad_steps_have_plain_english_errors():
    df = pd.DataFrame({"a": [1, 2]})
    with pytest.raises(ValueError, match="unknown step"):
        clean_dataframe(df, transforms=[{"op": "explode"}])
    with pytest.raises(ValueError, match="no column called 'b'"):
        clean_dataframe(df, transforms=[{"op": "change_type", "column": "b", "to": "date"}])
    with pytest.raises(ValueError, match="isn't a number"):
        clean_dataframe(df, transforms=[{"op": "replace_values", "column": "a", "find": "1", "replace": "x"}])


def test_a_negative_limit_starts_at_zero_when_nothing_is_negative():
    s = pd.Series([1, 2, 2, 3, 3, 3, 4, 4, 5, 6, 200])
    assert outlier_check(s)["low"] == 0


def test_dates_in_the_future_are_unusual():
    s = pd.Series(pd.to_datetime(["2024-01-01"] * 11 + ["2999-01-01"]))
    assert outlier_check(s)["count"] == 1
