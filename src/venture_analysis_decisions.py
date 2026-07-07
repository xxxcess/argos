"""Transparent, fact-based selection trails for Venture data-analysis briefings.

The planner selects from verified candidates. This module explains the visible
selection using column roles, objective-to-column matches, and profile signals.
It does not expose hidden model reasoning or treat model output as a fact.
"""
from __future__ import annotations

import re
from typing import Any

_TIME_WORDS = {"trend", "trends", "change", "changes", "growth", "decline", "forecast", "seasonality", "weekly", "monthly", "daily", "time"}
_RELATIONSHIP_WORDS = {"driver", "drivers", "relationship", "relationships", "correlation", "impact", "influence", "association"}
_EXCEPTION_WORDS = {"outlier", "outliers", "anomaly", "anomalies", "exception", "exceptions", "risk", "error", "errors"}
_QUALITY_WORDS = {"quality", "missing", "complete", "completeness", "duplicate", "duplicates", "clean", "cleanliness"}
_CATEGORY_WORDS = {"region", "regions", "segment", "segments", "customer", "customers", "product", "products", "category", "categories", "country", "countries", "market", "markets"}


def _tokens(value: str | None) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", str(value or "").casefold()) if len(token) > 1}


def _role_columns(schema: dict[str, Any], role: str) -> list[str]:
    return [str(name) for name in schema.get(f"{role}_columns") or [] if str(name)]


def _objective_matches(goal: str, schema: dict[str, Any]) -> list[str]:
    goal_tokens = _tokens(goal)
    matches: list[str] = []
    for column in schema.get("columns") or []:
        name = str(column.get("name") or "")
        if goal_tokens.intersection(_tokens(name)):
            matches.append(name)
    return matches[:5]


def _objective_decisions(goal: str, schema: dict[str, Any]) -> list[str]:
    if not goal:
        return []
    objective = _tokens(goal)
    decisions: list[str] = []
    matches = _objective_matches(goal, schema)
    if matches:
        decisions.append(f"Objective terms matched column(s): {', '.join(matches)}.")
    if objective.intersection(_TIME_WORDS):
        temporal = _role_columns(schema, "temporal")
        decisions.append(
            f"Your objective asks about change over time; {'temporal field(s) found: ' + ', '.join(temporal) if temporal else 'no temporal field was found, so time charts were not prioritized'}.")
    if objective.intersection(_RELATIONSHIP_WORDS):
        numeric = _role_columns(schema, "numeric")
        decisions.append(
            f"Your objective asks about drivers or relationships; {'multiple numeric fields support comparison' if len(numeric) >= 2 else 'fewer than two numeric fields limit relationship charts'}.")
    if objective.intersection(_CATEGORY_WORDS):
        categorical = _role_columns(schema, "categorical")
        decisions.append(
            f"Your objective asks for a breakdown; {'categorical field(s) found: ' + ', '.join(categorical[:4]) if categorical else 'no categorical field was found'}.")
    if objective.intersection(_EXCEPTION_WORDS):
        decisions.append("Your objective asks for exceptions, so outlier candidates are considered when numeric ranges are available.")
    if objective.intersection(_QUALITY_WORDS):
        decisions.append("Your objective asks about data quality, so completeness and missingness candidates are considered.")
    if not decisions:
        decisions.append("The stated objective is used to rank verified candidates alongside detected field roles.")
    return decisions[:5]


def _fields_for_visual(visual: dict[str, Any]) -> list[str]:
    spec = visual.get("spec") or {}
    fields: list[str] = []
    for key in ("field", "x_field", "y_field", "x_label", "y_label"):
        value = spec.get(key)
        if value and str(value) not in fields:
            fields.append(str(value))
    return fields[:4]


def _selection_label(source: str) -> tuple[str, str]:
    if source == "llm":
        return (
            "Configured planner selection",
            "A configured model selected from server-verified candidates; it did not generate data values or factual claims.",
        )
    if source == "saved":
        return (
            "Saved selection",
            "This tab is reusing the verified selection stored when the CSV was uploaded.",
        )
    return (
        "Deterministic selection",
        "A configured model was unavailable or not used, so Argos applied objective-aware deterministic ranking to verified candidates.",
    )


def _visual_reasons(visual: dict[str, Any], schema: dict[str, Any], insights: dict[str, Any], goal: str) -> list[str]:
    visual_id = str(visual.get("id") or "")
    fields = _fields_for_visual(visual)
    numeric = _role_columns(schema, "numeric")
    temporal = _role_columns(schema, "temporal")
    categorical = _role_columns(schema, "categorical")
    reasons: list[str] = []
    if visual_id in {"trend", "temporal-coverage"}:
        reasons.append(f"Detected temporal field(s): {', '.join(temporal[:3]) or 'not available'}.")
    elif visual_id in {"distribution", "boxplot"}:
        reasons.append(f"Detected numeric field(s): {', '.join(numeric[:3]) or 'not available'}.")
    elif visual_id == "top-categories":
        reasons.append(f"Detected categorical field(s): {', '.join(categorical[:3]) or 'not available'}.")
    elif visual_id in {"relationship", "correlation"}:
        pairs = insights.get("correlations") or []
        pair = pairs[0] if pairs else None
        reasons.append(
            f"Multiple numeric fields are available{'; strongest measured pair: ' + str(pair.get('left')) + ' and ' + str(pair.get('right')) if pair else ''}.")
    elif visual_id == "outliers":
        counts = [int(value or 0) for value in (insights.get("outlier_counts") or {}).values()]
        reasons.append(f"Possible outliers were detected in {sum(1 for value in counts if value > 0)} numeric field(s).")
    elif visual_id == "data-quality":
        reasons.append(f"Overall missing-cell rate is {float((insights.get('overview') or {}).get('missing_rate') or 0) * 100:.1f}%.")
    elif visual_id == "row-completeness":
        reasons.append("Row-level completeness is available from the profiled CSV.")
    elif visual_id == "cardinality":
        reasons.append("Distinct-value counts were calculated for the available columns.")
    elif visual_id == "field-profile":
        reasons.append("Column types were inferred during CSV profiling.")
    if fields:
        reasons.append(f"This visual uses: {', '.join(fields)}.")
    objective = _tokens(goal)
    field_tokens = _tokens(" ".join(fields))
    if goal and objective.intersection(field_tokens):
        reasons.append("Its fields directly match words in your stated objective.")
    elif goal and visual_id in {"trend", "temporal-coverage"} and objective.intersection(_TIME_WORDS):
        reasons.append("It supports the time/change part of your objective.")
    elif goal and visual_id in {"relationship", "correlation"} and objective.intersection(_RELATIONSHIP_WORDS):
        reasons.append("It supports the driver/relationship part of your objective.")
    elif goal and visual_id == "top-categories" and objective.intersection(_CATEGORY_WORDS):
        reasons.append("It supports the requested category or segment breakdown.")
    elif goal and visual_id in {"outliers", "boxplot"} and objective.intersection(_EXCEPTION_WORDS):
        reasons.append("It supports the requested exception review.")
    elif goal and visual_id in {"data-quality", "row-completeness"} and objective.intersection(_QUALITY_WORDS):
        reasons.append("It supports the requested data-quality review.")
    return reasons[:4] or ["Selected from the verified visualization candidates for this dataset."]


def _card_reasons(card: dict[str, Any], schema: dict[str, Any], insights: dict[str, Any], goal: str) -> list[str]:
    title = str(card.get("title") or "").casefold()
    tone = str(card.get("tone") or "")
    reasons: list[str] = []
    if tone == "goal" or title.startswith("goal lens") or title == "your analysis goal":
        reasons.append("This card is tied to your stated analysis objective.")
    if "quality" in title or "completeness" in title or "duplicate" in title:
        reasons.append(f"The profile found {float((insights.get('overview') or {}).get('missing_rate') or 0) * 100:.1f}% missing cells and checks data quality before comparisons.")
    elif "time" in title or "trend" in title:
        reasons.append(f"Temporal field(s) are available: {', '.join(_role_columns(schema, 'temporal')[:3]) or 'not detected'}.")
    elif "relationship" in title:
        reasons.append(f"At least {len(_role_columns(schema, 'numeric'))} numeric field(s) support measured relationships.")
    elif "outlier" in title or "exception" in title or "worth review" in title:
        reasons.append("The numeric profiling stage detected values outside the conventional IQR range.")
    elif "category" in title or "concentration" in title or "mix" in title:
        reasons.append(f"Categorical field(s) are available: {', '.join(_role_columns(schema, 'categorical')[:3]) or 'not detected'}.")
    elif "footprint" in title or "field mix" in title or "ready" in title:
        overview = insights.get("overview") or {}
        reasons.append(f"Based on the profiled shape: {int(overview.get('rows') or 0):,} rows and {int(overview.get('columns') or 0):,} columns.")
    if goal and tone != "goal":
        matches = _objective_matches(goal, schema)
        if matches:
            reasons.append(f"Objective-matched fields considered for ranking: {', '.join(matches[:3])}.")
    return reasons[:3] or ["Selected from the verified insight-card candidates for this dataset."]


def build_selection_decisions(payload: dict[str, Any]) -> dict[str, Any]:
    """Return transparent, non-sensitive evidence for the rendered selection."""
    schema = payload.get("schema") or {}
    insights = payload.get("insights") or {}
    briefing = payload.get("briefing") or {}
    goal = str((payload.get("session") or {}).get("analysis_goal") or briefing.get("analysis_goal") or "")
    planner = briefing.get("planner") or {}
    source = str(planner.get("source") or "heuristic")
    label, detail = _selection_label(source)
    overview = insights.get("overview") or {}
    return {
        "selection_source": source,
        "selection_label": label,
        "selection_detail": detail,
        "objective": {
            "provided": bool(goal),
            "text": goal,
            "matched_columns": _objective_matches(goal, schema),
            "decisions": _objective_decisions(goal, schema),
        },
        "dataset_signals": [
            f"Profiled {int(overview.get('rows') or schema.get('row_count') or 0):,} rows and {int(overview.get('columns') or schema.get('column_count') or 0):,} columns.",
            f"Detected {len(_role_columns(schema, 'numeric'))} numeric, {len(_role_columns(schema, 'temporal'))} temporal, and {len(_role_columns(schema, 'categorical'))} categorical field(s).",
            f"Overall missing-cell rate: {float(overview.get('missing_rate') or 0) * 100:.1f}%.",
        ],
        "visuals": [
            {"id": str(visual.get("id") or ""), "title": str(visual.get("title") or ""), "reasons": _visual_reasons(visual, schema, insights, goal)}
            for visual in payload.get("visuals") or []
        ],
        "cards": [
            {"id": str(card.get("id") or ""), "title": str(card.get("title") or ""), "reasons": _card_reasons(card, schema, insights, goal)}
            for card in briefing.get("cards") or []
        ],
    }


def decorate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Attach visible decision evidence without altering chart facts."""
    decisions = build_selection_decisions(payload)
    visual_reasons = {item["id"]: item["reasons"] for item in decisions["visuals"]}
    card_reasons = {item["id"]: item["reasons"] for item in decisions["cards"]}
    for visual in payload.get("visuals") or []:
        visual["selection_reasons"] = visual_reasons.get(str(visual.get("id") or ""), [])
    briefing = payload.setdefault("briefing", {})
    for card in briefing.get("cards") or []:
        card["selection_reasons"] = card_reasons.get(str(card.get("id") or ""), [])
    payload["selection_decisions"] = decisions
    return payload
