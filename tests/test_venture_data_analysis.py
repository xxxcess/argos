"""Focused tests for the deterministic Argos Venture data-analysis core."""

import pytest

pl = pytest.importorskip("polars")

from src.venture_data_analysis import (  # noqa: E402
    AnalysisError,
    _apply_prompt,
    _safe_filename,
    default_dashboard,
    profile,
)


def _frame():
    return pl.DataFrame({
        "region": ["East", "West", "East", "South"],
        "revenue": [100.0, 240.0, 130.0, 95.0],
        "units": [10, 20, 11, 8],
    })


def test_profile_returns_json_safe_overview_stats_and_categories():
    schema, insights = profile(_frame())

    assert schema["row_count"] == 4
    assert schema["column_count"] == 3
    assert {column["name"] for column in schema["columns"]} == {"region", "revenue", "units"}
    assert insights["overview"]["rows"] == 4
    assert insights["numeric_stats"]["revenue"]["mean"] == pytest.approx(141.25)
    assert insights["top_categories"]["region"][0] == {"value": "East", "count": 2}


def test_dashboard_prompt_changes_view_without_evaluating_user_code():
    frame = _frame()
    initial = default_dashboard(frame)

    scatter, scatter_message = _apply_prompt(frame, initial, "scatter revenue vs units")
    assert scatter["chart"] == {"kind": "scatter", "x": "revenue", "y": "units"}
    assert "scatter plot" in scatter_message

    filtered, filter_message = _apply_prompt(frame, scatter, "filter region = East")
    assert filtered["filters"] == [{"column": "region", "value": "East"}]
    assert "Filtered" in filter_message

    untouched, fallback_message = _apply_prompt(frame, filtered, "__import__('os').system('do-not-run')")
    assert untouched["chart"] == filtered["chart"]
    assert "Dashboard state is saved" in fallback_message


def test_csv_filename_is_confined_to_a_csv_basename():
    assert _safe_filename("../../quarterly sales.csv") == "quarterly_sales.csv"
    with pytest.raises(AnalysisError, match="CSV"):
        _safe_filename("report.xlsx")
