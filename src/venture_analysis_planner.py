"""Guarded LLM planning for Argos Venture data-analysis briefings.

The model receives metadata and a finite candidate list, then selects IDs from
that list. It never writes chart data, executable code, or free-form factual
claims. A deterministic selection always covers the dashboard when no planner
is configured or a model response is unavailable.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MIN_CHARTS = 4
MIN_INSIGHT_CARDS = 4
MAX_CHARTS = 6
MAX_INSIGHT_CARDS = 6
MAX_COLUMNS_FOR_PLANNER = 32


def _clean_endpoint(value: str) -> str:
    endpoint = (value or "").strip().rstrip("/")
    if endpoint and not endpoint.endswith("/chat/completions"):
        endpoint = f"{endpoint}/chat/completions"
    return endpoint


def _settings() -> dict[str, Any] | None:
    endpoint = _clean_endpoint(os.getenv("ARGOS_ANALYSIS_PLANNER_ENDPOINT", ""))
    model = os.getenv("ARGOS_ANALYSIS_PLANNER_MODEL", "").strip()
    if not endpoint or not model:
        return None
    timeout = max(2.0, min(float(os.getenv("ARGOS_ANALYSIS_PLANNER_TIMEOUT_SECONDS", "12")), 45.0))
    return {
        "endpoint": endpoint,
        "model": model,
        "api_key": os.getenv("ARGOS_ANALYSIS_PLANNER_API_KEY", "").strip(),
        "timeout": timeout,
    }


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").casefold()).strip("-") or "candidate"


def _unique_ids(candidates: list[dict[str, Any]], prefix: str) -> list[dict[str, Any]]:
    used: set[str] = set()
    result: list[dict[str, Any]] = []
    for position, candidate in enumerate(candidates, start=1):
        copy = dict(candidate)
        base = _slug(copy.get("id") or copy.get("title") or f"{prefix}-{position}")
        candidate_id = base
        suffix = 2
        while candidate_id in used:
            candidate_id = f"{base}-{suffix}"
            suffix += 1
        copy["id"] = candidate_id
        used.add(candidate_id)
        result.append(copy)
    return result


def annotate_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Assign stable identifiers while leaving the fact-checked card body intact."""
    return _unique_ids(cards, "insight")


def _visual_metadata(visual: dict[str, Any]) -> dict[str, Any]:
    spec = visual.get("spec") or {}
    fields = []
    for key in ("field", "x_field", "y_field", "x_label", "y_label"):
        value = spec.get(key)
        if value and value not in fields:
            fields.append(str(value))
    return {
        "id": str(visual.get("id") or ""),
        "kind": str(visual.get("kind") or ""),
        "title": str(visual.get("title") or ""),
        "fields": fields[:4],
    }


def _card_metadata(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(card.get("id") or ""),
        "title": str(card.get("title") or ""),
        "tone": str(card.get("tone") or "neutral"),
    }


def _column_metadata(schema: dict[str, Any]) -> list[dict[str, Any]]:
    numeric = set(schema.get("numeric_columns") or [])
    temporal = set(schema.get("temporal_columns") or [])
    categorical = set(schema.get("categorical_columns") or [])
    columns = []
    for column in (schema.get("columns") or [])[:MAX_COLUMNS_FOR_PLANNER]:
        name = str(column.get("name") or "")
        role = "numeric" if name in numeric else "temporal" if name in temporal else "categorical" if name in categorical else "other"
        columns.append({
            "name": name,
            "role": role,
            "dtype": str(column.get("dtype") or ""),
            "missing_rate": round(float(column.get("null_rate") or 0), 4),
        })
    return columns


def planning_context(
    schema: dict[str, Any],
    insights: dict[str, Any],
    goal: str,
    visuals: list[dict[str, Any]],
    cards: list[dict[str, Any]],
) -> dict[str, Any]:
    """Metadata-only context. Raw row values and category labels stay local."""
    overview = insights.get("overview") or {}
    return {
        "objective": goal or None,
        "dataset": {
            "row_count": int(overview.get("rows") or schema.get("row_count") or 0),
            "column_count": int(overview.get("columns") or schema.get("column_count") or 0),
            "missing_rate": round(float(overview.get("missing_rate") or 0), 4),
        },
        "columns": _column_metadata(schema),
        "candidate_visuals": [_visual_metadata(visual) for visual in visuals],
        "candidate_insight_cards": [_card_metadata(card) for card in cards],
        "selection_rules": {
            "minimum_charts": MIN_CHARTS,
            "maximum_charts": MAX_CHARTS,
            "minimum_insight_cards": MIN_INSIGHT_CARDS,
            "maximum_insight_cards": MAX_INSIGHT_CARDS,
            "only_choose_provided_ids": True,
        },
    }


def _extract_json(content: Any) -> dict[str, Any] | None:
    if isinstance(content, dict):
        return content
    if isinstance(content, list):
        content = "".join(str(part.get("text") or "") if isinstance(part, dict) else str(part) for part in content)
    text = str(content or "").strip()
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except (TypeError, ValueError):
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else None
        except (TypeError, ValueError):
            return None


def _post_completion(settings: dict[str, Any], context: dict[str, Any], response_format: bool) -> dict[str, Any] | None:
    system_prompt = (
        "You are Argos's data-analysis display planner. Choose the most useful "
        "charts and insight cards from the supplied IDs only. Use the stated "
        "objective, column names, types, and data-quality metadata. Return JSON "
        "with exactly two arrays: visual_ids and card_ids. Select 4 to 6 IDs in "
        "each array. Do not create IDs, claims, values, or chart specifications."
    )
    payload: dict[str, Any] = {
        "model": settings["model"],
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False, separators=(",", ":"))},
        ],
    }
    if response_format:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Content-Type": "application/json"}
    if settings.get("api_key"):
        headers["Authorization"] = f"Bearer {settings['api_key']}"
    request = Request(
        settings["endpoint"],
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urlopen(request, timeout=settings["timeout"]) as response:
        result = json.loads(response.read().decode("utf-8"))
    choices = result.get("choices") or []
    message = (choices[0] or {}).get("message") if choices else None
    return _extract_json((message or {}).get("content"))


def _ask_model(context: dict[str, Any]) -> dict[str, Any] | None:
    settings = _settings()
    if not settings:
        return None
    try:
        try:
            return _post_completion(settings, context, response_format=True)
        except HTTPError as exc:
            if exc.code not in {400, 404, 422}:
                return None
            return _post_completion(settings, context, response_format=False)
    except (HTTPError, URLError, OSError, TimeoutError, ValueError, json.JSONDecodeError):
        return None


def _selected_ids(
    requested: Any,
    candidates: list[dict[str, Any]],
    minimum: int,
    maximum: int,
) -> tuple[list[str], bool]:
    available = [str(candidate.get("id") or "") for candidate in candidates if candidate.get("id")]
    available_set = set(available)
    selected: list[str] = []
    valid_response = False
    if isinstance(requested, list):
        for value in requested:
            candidate_id = str(value or "")
            if candidate_id in available_set and candidate_id not in selected:
                selected.append(candidate_id)
                valid_response = True
            if len(selected) == maximum:
                break
    for candidate_id in available:
        if len(selected) >= minimum:
            break
        if candidate_id not in selected:
            selected.append(candidate_id)
    return selected[:maximum], valid_response


def resolve_plan(
    schema: dict[str, Any],
    insights: dict[str, Any],
    goal: str,
    visuals: list[dict[str, Any]],
    cards: list[dict[str, Any]],
    saved_plan: dict[str, Any] | None = None,
    *,
    invoke_llm: bool = False,
) -> dict[str, Any]:
    """Validate a saved/model selection and always enforce the four-item floor."""
    visuals = _unique_ids(visuals, "visual")
    cards = _unique_ids(cards, "insight")
    response = saved_plan if isinstance(saved_plan, dict) else None
    source = "saved" if response else "heuristic"
    if response is None and invoke_llm:
        response = _ask_model(planning_context(schema, insights, goal, visuals, cards))
        source = "llm" if response else "heuristic"

    visual_ids, visual_valid = _selected_ids(
        (response or {}).get("visual_ids"),
        visuals,
        MIN_CHARTS,
        MAX_CHARTS,
    )
    card_ids, card_valid = _selected_ids(
        (response or {}).get("card_ids"),
        cards,
        MIN_INSIGHT_CARDS,
        MAX_INSIGHT_CARDS,
    )
    if source == "llm" and not (visual_valid and card_valid):
        source = "heuristic"
    if source == "saved" and not (visual_valid or card_valid):
        source = "heuristic"
    return {
        "version": 1,
        "source": source,
        "visual_ids": visual_ids,
        "card_ids": card_ids,
        "minimums": {"charts": MIN_CHARTS, "insight_cards": MIN_INSIGHT_CARDS},
    }


def select_candidates(candidates: list[dict[str, Any]], selected_ids: list[str]) -> list[dict[str, Any]]:
    by_id = {str(candidate.get("id") or ""): candidate for candidate in candidates}
    return [by_id[candidate_id] for candidate_id in selected_ids if candidate_id in by_id]
