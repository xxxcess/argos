"""Focused tests for Argos Venture's automatic ECharts insight briefing."""

import pytest

pl = pytest.importorskip("polars")

from src.venture_data_analysis import AnalysisError, _safe_filename  # noqa: E402
from src.venture_data_briefing import build_briefing, normalize_goal, profile_frame  # noqa: E402
from src.venture_echarts_briefing import build_echarts_visuals  # noqa: E402


def _frame():
    return pl.DataFrame({
        "date": ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04", "2026-01-04"],
        "region": ["East", "West", "East", "South", "South"],
        "revenue": [100.0, 240.0, 130.0, 95.0, 95.0],
        "units": [10, 20, 11, 8, 8],
        "note": [None, "ok", None, "ok", "ok"],
    }).with_columns(pl.col("date").str.strptime(pl.Date, "%Y-%m-%d"))


def test_profile_adds_time_and_relationship_signals_to_the_base_summary():
    schema, insights = profile_frame(_frame())

    assert schema["row_count"] == 5
    assert "date" in schema["temporal_columns"]
    assert insights["overview"]["missing_cells"] == 2
    assert insights["top_categories"]["region"][0] == {"value": "East", "count": 2}
    assert insights["time_series"]["measure"] == "revenue"
    assert insights["correlations"]


def test_goal_aware_briefing_adds_domain_cards_without_causal_claims():
    schema, insights = profile_frame(_frame())
    briefing = build_briefing(schema, insights, "Analyze revenue trend and region changes")

    assert briefing["analysis_goal"] == "Analyze revenue trend and region changes"
    assert briefing["goal_cards"]
    assert any(card["title"] == "Goal lens · commercial performance" for card in briefing["goal_cards"])
    assert any(card["title"] == "Goal lens · geography mix" for card in briefing["goal_cards"])
    relationships = [card for card in briefing["cards"] if card["title"] == "Numeric relationship"]
    assert relationships and "association, not causation" in relationships[0]["detail"]


def test_visuals_use_small_echarts_contracts_instead_of_server_images():
    frame = _frame()
    schema, insights = profile_frame(frame)
    visuals = build_echarts_visuals(frame, schema, insights)

    assert visuals
    assert {visual["renderer"] for visual in visuals} == {"echarts"}
    assert all("spec" in visual for visual in visuals)
    assert all("image_png_base64" not in visual for visual in visuals)
    assert any(visual["kind"] == "histogram" for visual in visuals)
    assert any(visual["kind"] == "boxplot" for visual in visuals)
    assert any(visual["kind"] == "scatter" for visual in visuals)
    assert any(visual["kind"] == "heatmap" for visual in visuals)


def test_goal_is_normalized_and_csv_filename_is_confined():
    assert normalize_goal("  find   revenue  drivers ") == "find revenue drivers"
    assert _safe_filename("../../quarterly sales.csv") == "quarterly_sales.csv"
    with pytest.raises(AnalysisError, match="CSV"):
        _safe_filename("report.xlsx")
