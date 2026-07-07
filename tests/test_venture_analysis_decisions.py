"""Tests for transparent, objective-aware Venture analysis selection trails."""

import pytest

pl = pytest.importorskip("polars")

from src.venture_analysis_decisions import decorate_payload  # noqa: E402
from src.venture_analysis_planner import resolve_plan  # noqa: E402
from src.venture_echarts_briefing import build_echarts_report  # noqa: E402


def _frame():
    return pl.DataFrame({
        "date": ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04", "2026-01-04"],
        "region": ["East", "West", "East", "South", "South"],
        "revenue": [100.0, 240.0, 130.0, 95.0, 95.0],
        "units": [10, 20, 11, 8, 8],
        "note": [None, "ok", None, "ok", "ok"],
    }).with_columns(pl.col("date").str.strptime(pl.Date, "%Y-%m-%d"))


def test_objective_aware_fallback_elevates_goal_cards():
    visuals = [{"id": f"visual-{index}", "title": f"Visual {index}", "kind": "bar"} for index in range(1, 7)]
    cards = [
        {"id": "footprint", "title": "Dataset footprint", "tone": "neutral"},
        {"id": "goal", "title": "Goal lens · commercial performance", "tone": "goal"},
        {"id": "quality", "title": "Data quality", "tone": "neutral"},
        {"id": "mix", "title": "Field mix", "tone": "neutral"},
        {"id": "relationship", "title": "Numeric relationship", "tone": "neutral"},
    ]
    plan = resolve_plan({}, {}, "Find revenue drivers", visuals, cards, invoke_llm=False)

    assert plan["source"] == "heuristic"
    assert plan["card_ids"][0] == "goal"
    assert len(plan["visual_ids"]) >= 4
    assert len(plan["card_ids"]) >= 4


def test_decision_payload_explains_objective_column_matches_and_each_selection():
    goal = "Identify revenue drivers and region changes"
    report = build_echarts_report(_frame(), goal, invoke_llm=False)
    payload = decorate_payload({
        "session": {"analysis_goal": goal},
        "schema": report["schema"],
        "insights": report["insights"],
        "briefing": report["briefing"],
        "visuals": report["visuals"],
    })

    decisions = payload["selection_decisions"]
    assert decisions["objective"]["provided"] is True
    assert "revenue" in decisions["objective"]["matched_columns"]
    assert "region" in decisions["objective"]["matched_columns"]
    assert decisions["objective"]["decisions"]
    assert decisions["visuals"] and decisions["cards"]
    assert all(item["reasons"] for item in decisions["visuals"])
    assert all(item["reasons"] for item in decisions["cards"])
    assert all("selection_reasons" in visual for visual in payload["visuals"])
    assert all("selection_reasons" in card for card in payload["briefing"]["cards"])
