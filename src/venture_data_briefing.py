"""Deterministic, goal-aware insight briefings for Venture CSV workspaces."""
from __future__ import annotations

import base64
import json
import re
from math import sqrt
from typing import Any

from sqlalchemy import text

from core.database import engine
from src import venture_data_analysis as analysis

MAX_GOAL_CHARS = 600

GOAL_WORDS: dict[str, tuple[str, ...]] = {
    "commercial": ("revenue", "sales", "profit", "margin", "cost", "price", "amount", "spend", "budget"),
    "trend": ("trend", "growth", "change", "forecast", "time", "weekly", "monthly", "daily", "seasonality"),
    "customer": ("customer", "client", "user", "account", "churn", "retention", "cohort", "audience"),
    "funnel": ("conversion", "funnel", "stage", "status", "lead", "pipeline", "activation"),
    "product": ("product", "item", "sku", "category", "service", "plan", "package"),
    "geography": ("region", "country", "state", "city", "location", "market", "territory"),
    "operations": ("order", "unit", "quantity", "inventory", "stock", "shipment", "delivery", "fulfillment"),
    "quality": ("rating", "score", "satisfaction", "nps", "quality", "complaint", "support", "response"),
    "exceptions": ("outlier", "anomaly", "exception", "risk", "fraud", "error", "issue"),
}


def normalize_goal(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())[:MAX_GOAL_CHARS]


def ensure_goal_schema() -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS venture_analysis_goals (
                session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
                owner TEXT NOT NULL DEFAULT '',
                goal TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_venture_analysis_goal_owner
            ON venture_analysis_goals(owner, updated_at)
        """))


def save_goal(session_id: str, owner: str | None, goal: str | None) -> None:
    ensure_goal_schema()
    now = analysis._now()
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO venture_analysis_goals (session_id, owner, goal, updated_at)
            VALUES (:session_id, :owner, :goal, :updated_at)
            ON CONFLICT(session_id) DO UPDATE SET
                owner = excluded.owner,
                goal = excluded.goal,
                updated_at = excluded.updated_at
        """), {
            "session_id": session_id,
            "owner": analysis._owner(owner),
            "goal": normalize_goal(goal),
            "updated_at": now,
        })


def get_goal(session_id: str, owner: str | None) -> str:
    ensure_goal_schema()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT goal FROM venture_analysis_goals
            WHERE session_id = :session_id AND owner = :owner
        """), {
            "session_id": session_id,
            "owner": analysis._owner(owner),
        }).mappings().first()
    return normalize_goal(row["goal"] if row else "")


def _tokens(value: str | None) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", str(value or "").casefold()) if len(token) > 1}


def _domains(goal: str) -> list[str]:
    tokens = _tokens(goal)
    return [name for name, words in GOAL_WORDS.items() if tokens.intersection(words)]


def _temporal(dtype: Any) -> bool:
    return str(dtype).casefold().startswith(("date", "datetime", "time"))


def _pearson(frame, left: str, right: str) -> float | None:
    pl, _, _ = analysis._require_libraries()
    try:
        points = frame.select([
            pl.col(left).cast(pl.Float64).alias("left"),
            pl.col(right).cast(pl.Float64).alias("right"),
        ]).drop_nulls().head(50_000)
        xs = [float(value) for value in points.get_column("left").to_list()]
        ys = [float(value) for value in points.get_column("right").to_list()]
        if len(xs) < 3:
            return None
        x_mean = sum(xs) / len(xs)
        y_mean = sum(ys) / len(ys)
        numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
        x_scale = sqrt(sum((x - x_mean) ** 2 for x in xs))
        y_scale = sqrt(sum((y - y_mean) ** 2 for y in ys))
        return round(numerator / (x_scale * y_scale), 4) if x_scale and y_scale else None
    except Exception:
        return None


def _time_series(frame, temporal: list[str], numeric: list[str]) -> dict[str, Any] | None:
    if not temporal or not numeric:
        return None
    pl, _, _ = analysis._require_libraries()
    date_column, measure = temporal[0], numeric[0]
    try:
        values = frame.select([
            pl.col(date_column).cast(pl.Date).alias("date"),
            pl.col(measure).cast(pl.Float64).alias("value"),
        ]).drop_nulls()
        grouped = values.group_by("date").agg([
            pl.col("value").mean().alias("value"),
            pl.col("value").count().alias("count"),
        ]).sort("date").to_dicts()
        if len(grouped) < 2:
            return None
        if len(grouped) > 120:
            step = max(1, (len(grouped) + 119) // 120)
            grouped = grouped[::step]
        return {
            "date_column": date_column,
            "measure": measure,
            "points": [
                {"date": str(item["date"]), "value": round(float(item["value"]), 6), "count": int(item["count"] or 0)}
                for item in grouped if item.get("date") is not None and item.get("value") is not None
            ],
        }
    except Exception:
        return None


def profile_frame(frame) -> tuple[dict[str, Any], dict[str, Any]]:
    """Extend the base profiler with temporal and correlation signals."""
    schema, insights = analysis.profile(frame)
    numeric = list(schema.get("numeric_columns") or [item["name"] for item in schema.get("columns", []) if item.get("numeric")])
    temporal = [name for name, dtype in frame.schema.items() if _temporal(dtype)]
    categorical = [
        item["name"] for item in schema.get("columns", [])
        if not item.get("numeric") and item["name"] not in temporal
    ]
    schema.update({
        "numeric_columns": numeric,
        "temporal_columns": temporal,
        "categorical_columns": categorical,
    })
    pairs = []
    for index, left in enumerate(numeric[:6]):
        for right in numeric[index + 1:6]:
            value = _pearson(frame, left, right)
            if value is not None:
                pairs.append({"left": left, "right": right, "value": value})
    insights["correlations"] = sorted(pairs, key=lambda item: abs(float(item["value"])), reverse=True)
    insights["time_series"] = _time_series(frame, temporal, numeric)
    return schema, insights


def _find_columns(schema: dict[str, Any], words: tuple[str, ...], numeric_only: bool = False) -> list[str]:
    found = []
    for column in schema.get("columns") or []:
        if numeric_only and not column.get("numeric"):
            continue
        if _tokens(column.get("name")).intersection(words):
            found.append(column["name"])
    return found


def _goal_cards(schema: dict[str, Any], insights: dict[str, Any], goal: str) -> list[dict[str, Any]]:
    if not goal:
        return []
    overview = insights.get("overview") or {}
    rows = max(1, int(overview.get("rows") or 0))
    cards = [{
        "tone": "goal",
        "title": "Your analysis goal",
        "metric": "Goal-aware briefing",
        "detail": f"Argos is prioritizing patterns relevant to: {goal}",
        "action": "Review goal-specific findings",
    }]
    domains = _domains(goal)
    stats = insights.get("numeric_stats") or {}
    categories = insights.get("top_categories") or {}

    if "commercial" in domains:
        metric = next((name for name in _find_columns(schema, GOAL_WORDS["commercial"], True) if name in stats), None)
        if metric:
            values = stats[metric]
            cards.append({
                "tone": "goal",
                "title": "Goal lens · commercial performance",
                "metric": f"Avg {metric}: {float(values.get('mean') or 0):,.2f}",
                "detail": f"{metric} ranges from {float(values.get('min') or 0):,.2f} to {float(values.get('max') or 0):,.2f} in the profiled records.",
                "action": f"Review {metric} distribution",
            })

    series = insights.get("time_series") or {}
    points = series.get("points") or []
    if "trend" in domains and len(points) >= 2:
        first, last = points[0], points[-1]
        start, end = float(first["value"]), float(last["value"])
        percent = ((end - start) / abs(start) * 100) if start else None
        cards.append({
            "tone": "goal",
            "title": "Goal lens · change over time",
            "metric": f"{percent:+.1f}%" if percent is not None else f"{end - start:+.2f}",
            "detail": f"Average {series.get('measure')} changed between {first.get('date')} and {last.get('date')}.",
            "action": "Inspect the trend",
        })

    category_domains = {
        "customer": GOAL_WORDS["customer"],
        "funnel": GOAL_WORDS["funnel"],
        "product": GOAL_WORDS["product"],
        "geography": GOAL_WORDS["geography"],
        "operations": GOAL_WORDS["operations"],
    }
    for domain, words in category_domains.items():
        if domain not in domains:
            continue
        columns = _find_columns(schema, words)
        column = next((name for name in columns if categories.get(name)), None)
        if not column:
            continue
        top = categories[column][0]
        share = int(top.get("count") or 0) / rows
        cards.append({
            "tone": "goal",
            "title": f"Goal lens · {domain} mix",
            "metric": f"{share * 100:.0f}% in {column}",
            "detail": f"{top.get('value')} is the leading recorded value with {int(top.get('count') or 0):,} row(s).",
            "action": f"Compare {column}",
        })

    if "quality" in domains:
        metric = next((name for name in _find_columns(schema, GOAL_WORDS["quality"], True) if name in stats), None)
        if metric:
            values = stats[metric]
            cards.append({
                "tone": "goal",
                "title": "Goal lens · quality signal",
                "metric": f"Median {metric}: {float(values.get('median') or 0):,.2f}",
                "detail": f"Observed values range from {float(values.get('min') or 0):,.2f} to {float(values.get('max') or 0):,.2f}.",
                "action": f"Inspect {metric}",
            })

    if "exceptions" in domains:
        outliers = [(column, int(count)) for column, count in (insights.get("outlier_counts") or {}).items() if int(count or 0) > 0]
        if outliers:
            column, count = max(outliers, key=lambda item: item[1])
            cards.append({
                "tone": "warning",
                "title": "Goal lens · exceptions",
                "metric": f"{count:,} possible outliers",
                "detail": f"{column} has values outside the conventional 1.5× IQR range.",
                "action": "Inspect exceptions",
            })
    return cards[:4]


def build_briefing(schema: dict[str, Any], insights: dict[str, Any], goal: str | None = None) -> dict[str, Any]:
    overview = insights.get("overview") or {}
    rows = int(overview.get("rows") or schema.get("row_count") or 0)
    missing_rate = float(overview.get("missing_rate") or 0)
    cards, summary = [], []
    completeness = max(0.0, 1 - missing_rate)

    if missing_rate >= 0.10:
        cards.append({
            "tone": "warning", "title": "Data-quality warning", "metric": f"{missing_rate * 100:.1f}% missing",
            "detail": "Missing values are substantial enough to affect comparisons across fields.",
            "action": "Review incomplete columns",
        })
        summary.append(f"The dataset is {completeness * 100:.1f}% complete; missing values should be considered before comparing segments.")
    else:
        cards.append({
            "tone": "positive", "title": "Dataset health", "metric": f"{completeness * 100:.1f}% complete",
            "detail": "Most cells contain values, providing a stable base for descriptive exploration.",
            "action": "Inspect coverage by column",
        })
        summary.append(f"The dataset contains {rows:,} row(s) across {int(overview.get('columns') or 0):,} column(s) and is mostly complete.")

    if overview.get("duplicate_checked") and overview.get("duplicate_rows"):
        cards.append({
            "tone": "warning", "title": "Repeated records", "metric": f"{int(overview['duplicate_rows']):,} duplicate rows",
            "detail": "Exact duplicate records may inflate totals or category counts.",
            "action": "Inspect duplicates",
        })

    categories = insights.get("top_categories") or {}
    for column, values in categories.items():
        if values and rows:
            top = values[0]
            share = int(top.get("count") or 0) / rows
            cards.append({
                "tone": "neutral", "title": "Category concentration", "metric": f"{share * 100:.0f}% in {column}",
                "detail": f"{top.get('value')} is the leading recorded value with {int(top.get('count') or 0):,} row(s).",
                "action": f"Explore {column}",
            })
            summary.append(f"{column} has a leading value: {top.get('value')} represents {share * 100:.1f}% of recorded rows.")
            break

    series = insights.get("time_series") or {}
    points = series.get("points") or []
    if len(points) >= 2:
        first, last = points[0], points[-1]
        delta = float(last["value"]) - float(first["value"])
        direction = "increased" if delta > 0 else "decreased" if delta < 0 else "was unchanged"
        cards.append({
            "tone": "neutral", "title": "Observed time pattern", "metric": f"{abs(delta):,.2f}",
            "detail": f"Average {series.get('measure')} {direction} from {first.get('date')} to {last.get('date')}.",
            "action": "Review the trend",
        })
        summary.append(f"Average {series.get('measure')} {direction} across the observed dates.")

    correlations = insights.get("correlations") or []
    if correlations and abs(float(correlations[0]["value"])) >= 0.35:
        item = correlations[0]
        cards.append({
            "tone": "neutral", "title": "Numeric relationship", "metric": f"r = {float(item['value']):.2f}",
            "detail": f"{item['left']} and {item['right']} move together in the sampled records; this is association, not causation.",
            "action": "Compare the relationship",
        })

    outliers = [(column, int(count)) for column, count in (insights.get("outlier_counts") or {}).items() if int(count or 0) > 0]
    if outliers:
        column, count = max(outliers, key=lambda item: item[1])
        cards.append({
            "tone": "warning", "title": "Values worth review", "metric": f"{count:,} possible outliers",
            "detail": f"{column} contains values outside the typical 1.5× IQR range.",
            "action": "Inspect exceptions",
        })
        summary.append(f"{count:,} value(s) in {column} fall outside the typical observed range and merit review.")

    if not summary:
        summary.append("No strong automatic pattern was found yet; inspect the distributions and category breakdowns for additional context.")

    goal = normalize_goal(goal)
    next_step = (
        "Review incomplete fields before comparing segments."
        if missing_rate >= 0.10 else
        "Inspect the outlier records before using the dataset for forecasting or planning."
        if outliers else
        "Compare the strongest category and numeric patterns before forming a decision."
    )
    return {
        "headline": "What Argo found",
        "analysis_goal": goal,
        "summary": summary[:4],
        "cards": cards[:5],
        "goal_cards": _goal_cards(schema, insights, goal),
        "recommended_next_step": next_step,
    }


def _chart(figure, visual_id: str, title: str, kind: str, summary: str) -> dict[str, Any]:
    _, _, pio = analysis._require_libraries()
    figure.update_layout(
        margin={"l": 44, "r": 20, "t": 55, "b": 52},
        height=350,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    try:
        figure_json = json.loads(pio.to_json(figure, validate=False))
    except Exception:
        figure_json = {}
    try:
        image = base64.b64encode(pio.to_image(figure, format="png", width=920, height=420, scale=1)).decode("ascii")
        image_error = None
    except Exception as exc:
        image, image_error = None, str(exc)
    return {
        "id": visual_id, "title": title, "kind": kind, "summary": summary,
        "figure": figure_json, "image_png_base64": image, "image_error": image_error,
    }


def build_visuals(frame, schema: dict[str, Any], insights: dict[str, Any]) -> list[dict[str, Any]]:
    _, go, _ = analysis._require_libraries()
    visuals = []
    series = insights.get("time_series") or {}
    points = series.get("points") or []
    if len(points) >= 2:
        fig = go.Figure(go.Scatter(x=[item["date"] for item in points], y=[item["value"] for item in points], mode="lines+markers"))
        fig.update_layout(title=f"Average {series.get('measure')} over time", xaxis_title=series.get("date_column"), yaxis_title=f"Average {series.get('measure')}")
        visuals.append(_chart(fig, "trend", "Trend over time", "line", f"Average {series.get('measure')} by {series.get('date_column')}."))

    numeric = schema.get("numeric_columns") or []
    if numeric:
        column = numeric[0]
        values = frame.get_column(column).drop_nulls().head(50_000).to_list()
        if values:
            fig = go.Figure(go.Histogram(x=values, nbinsx=30))
            fig.update_layout(title=f"Distribution of {column}", xaxis_title=column, yaxis_title="Rows")
            visuals.append(_chart(fig, "distribution", "Distribution", "histogram", f"Distribution of non-empty {column} values."))

    for column, values in (insights.get("top_categories") or {}).items():
        if values:
            values = values[:10]
            fig = go.Figure(go.Bar(x=[int(item["count"]) for item in values][::-1], y=[str(item["value"]) for item in values][::-1], orientation="h"))
            fig.update_layout(title=f"Most frequent {column} values", xaxis_title="Rows", yaxis_title=column)
            visuals.append(_chart(fig, "top-categories", "Top categories", "bar", f"The ten most frequent values in {column}."))
            break

    missing = [item for item in schema.get("columns") or [] if int(item.get("null_count") or 0) > 0]
    if missing:
        missing = sorted(missing, key=lambda item: float(item.get("null_rate") or 0), reverse=True)[:10]
        fig = go.Figure(go.Bar(x=[round(float(item["null_rate"]) * 100, 2) for item in missing][::-1], y=[item["name"] for item in missing][::-1], orientation="h"))
        fig.update_layout(title="Missing values by column", xaxis_title="Missing values (%)", yaxis_title="Column")
        visuals.append(_chart(fig, "data-quality", "Data quality", "bar", "Columns with the highest share of missing values."))

    if len(numeric) >= 2:
        labels = numeric[:6]
        matrix = [[1.0 if left == right else (_pearson(frame, left, right) or 0.0) for right in labels] for left in labels]
        fig = go.Figure(go.Heatmap(z=matrix, x=labels, y=labels, zmin=-1, zmax=1))
        fig.update_layout(title="Numeric relationships", xaxis_title="Column", yaxis_title="Column")
        visuals.append(_chart(fig, "correlation", "Numeric relationships", "heatmap", "Pairwise correlation; association does not establish causation."))

    outliers = [(column, int(count)) for column, count in (insights.get("outlier_counts") or {}).items() if int(count or 0) > 0]
    if outliers:
        outliers = sorted(outliers, key=lambda item: item[1], reverse=True)[:10]
        fig = go.Figure(go.Bar(x=[count for _, count in outliers][::-1], y=[column for column, _ in outliers][::-1], orientation="h"))
        fig.update_layout(title="Possible outliers by column", xaxis_title="Rows outside 1.5× IQR", yaxis_title="Column")
        visuals.append(_chart(fig, "outliers", "Exceptions worth review", "bar", "Rows outside the conventional 1.5× IQR threshold."))
    return visuals[:6]


def empty_briefing(goal: str | None = None) -> dict[str, Any]:
    goal = normalize_goal(goal)
    return {
        "headline": "What Argo will look for",
        "analysis_goal": goal,
        "summary": [f"After profiling the CSV, Argos will prioritize: {goal}" if goal else "Upload a CSV to generate a data-health summary, tailored insight cards, and visual analysis."],
        "cards": [],
        "goal_cards": [],
        "recommended_next_step": "Upload a CSV dataset to begin the automatic briefing.",
    }


def build_report(frame, goal: str | None = None) -> dict[str, Any]:
    schema, insights = profile_frame(frame)
    return {
        "schema": schema,
        "insights": insights,
        "briefing": build_briefing(schema, insights, goal),
        "visuals": build_visuals(frame, schema, insights),
    }


def analysis_payload(session_id: str, owner: str | None) -> dict[str, Any]:
    """Return an enriched payload without using the retired prompt dashboard."""
    row = analysis._row(session_id, owner)
    goal = get_goal(session_id, owner)
    report = None
    if row.get("dataset_path"):
        report = build_report(analysis.read_csv(row["dataset_path"]), goal)
    if report:
        schema, insights = report["schema"], report["insights"]
        briefing, visuals = report["briefing"], report["visuals"]
    else:
        schema, insights, briefing, visuals = {}, {}, empty_briefing(goal), []
    return {
        "session": {
            "id": row["session_id"], "name": row["session_name"],
            "dataset_filename": row.get("dataset_filename"),
            "dataset_size_bytes": int(row.get("dataset_size_bytes") or 0),
            "analysis_goal": goal,
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        },
        "schema": schema, "insights": insights, "briefing": briefing, "visuals": visuals,
        "dashboard": {"revision": 2, "layout": "automatic_insight_briefing", "analysis_goal": goal},
        "chart": visuals[0] if visuals else None,
    }
