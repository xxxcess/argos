"""Compact ECharts contracts for Argos Venture data-analysis briefings.

This module keeps Polars profiling on the server and returns only small,
validated visualization inputs. It deliberately does not construct Plotly
figures or server-rendered image blobs.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

from sqlalchemy import text

from core.database import engine
from src import venture_data_analysis as analysis
from src import venture_data_briefing as briefing

MAX_VISUAL_POINTS = 10_000
MAX_HISTOGRAM_VALUES = 50_000


def _visual(visual_id: str, title: str, kind: str, summary: str, spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": visual_id,
        "title": title,
        "renderer": "echarts",
        "kind": kind,
        "summary": summary,
        "spec": spec,
    }


def _numeric_values(frame, column: str, limit: int = MAX_HISTOGRAM_VALUES) -> list[float]:
    try:
        values = [float(value) for value in frame.get_column(column).drop_nulls().head(limit).to_list()]
        return [value for value in values if math.isfinite(value)]
    except Exception:
        return []


def _histogram(values: list[float], bins: int = 28) -> dict[str, Any] | None:
    if not values:
        return None
    low, high = min(values), max(values)
    if low == high:
        return {"labels": [f"{low:g}"], "counts": [len(values)], "value_count": len(values)}
    bins = max(6, min(bins, int(math.sqrt(len(values))) or 6))
    width = (high - low) / bins
    counts = [0] * bins
    for value in values:
        index = min(bins - 1, max(0, int((value - low) / width)))
        counts[index] += 1
    return {
        "labels": [f"{low + index * width:.4g}–{low + (index + 1) * width:.4g}" for index in range(bins)],
        "counts": counts,
        "value_count": len(values),
    }


def _box_summary(values: list[float]) -> list[float] | None:
    if not values:
        return None
    ordered = sorted(values)

    def percentile(fraction: float) -> float:
        offset = (len(ordered) - 1) * fraction
        lower, upper = math.floor(offset), math.ceil(offset)
        if lower == upper:
            return ordered[lower]
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (offset - lower)

    return [round(percentile(fraction), 6) for fraction in (0, 0.25, 0.5, 0.75, 1)]


def _scatter_spec(frame, insights: dict[str, Any]) -> dict[str, Any] | None:
    pairs = insights.get("correlations") or []
    if not pairs:
        return None
    pair = pairs[0]
    left, right = pair["left"], pair["right"]
    pl, _, _ = analysis._require_libraries()
    try:
        points = frame.select([
            pl.col(left).cast(pl.Float64).alias("x"),
            pl.col(right).cast(pl.Float64).alias("y"),
        ]).drop_nulls().head(MAX_VISUAL_POINTS)
        xs = [float(value) for value in points.get_column("x").to_list()]
        ys = [float(value) for value in points.get_column("y").to_list()]
        if len(xs) < 3:
            return None

        def bounds(values: list[float]) -> tuple[float, float]:
            sorted_values = sorted(values)

            def percentile(fraction: float) -> float:
                offset = (len(sorted_values) - 1) * fraction
                lower, upper = math.floor(offset), math.ceil(offset)
                return sorted_values[lower] if lower == upper else sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (offset - lower)

            q1, q3 = percentile(0.25), percentile(0.75)
            spread = q3 - q1
            return q1 - 1.5 * spread, q3 + 1.5 * spread

        x_low, x_high = bounds(xs)
        y_low, y_high = bounds(ys)
        regular, exceptions = [], []
        for x, y in zip(xs, ys):
            target = exceptions if (x < x_low or x > x_high or y < y_low or y > y_high) else regular
            target.append([round(x, 6), round(y, 6)])
        return {
            "x_field": left,
            "y_field": right,
            "correlation": pair["value"],
            "regular": regular,
            "exceptions": exceptions,
            "point_count": len(xs),
        }
    except Exception:
        return None


def build_echarts_visuals(frame, schema: dict[str, Any], insights: dict[str, Any]) -> list[dict[str, Any]]:
    """Build the interactive chart set from compact, trusted server aggregates."""
    visuals: list[dict[str, Any]] = []
    series = insights.get("time_series") or {}
    points = series.get("points") or []
    if len(points) >= 2:
        visuals.append(_visual(
            "trend", "Trend over time", "line",
            f"Average {series.get('measure')} by {series.get('date_column')}.",
            {
                "x": [item["date"] for item in points],
                "y": [item["value"] for item in points],
                "x_label": series.get("date_column"),
                "y_label": f"Average {series.get('measure')}",
                "point_count": len(points),
            },
        ))

    numeric = schema.get("numeric_columns") or []
    if numeric:
        column = numeric[0]
        values = _numeric_values(frame, column)
        histogram = _histogram(values)
        box = _box_summary(values)
        if histogram:
            visuals.append(_visual(
                "distribution", "Distribution", "histogram",
                f"Distribution of non-empty {column} values.",
                {"field": column, "box": box, **histogram},
            ))
        if box:
            visuals.append(_visual(
                "boxplot", "Range and outliers", "boxplot",
                f"Five-number summary for {column}; whiskers show the observed range.",
                {"field": column, "values": box, "point_count": len(values)},
            ))

    for column, values in (insights.get("top_categories") or {}).items():
        if values:
            values = values[:10]
            visuals.append(_visual(
                "top-categories", "Top categories", "bar",
                f"The ten most frequent values in {column}.",
                {
                    "field": column,
                    "labels": [str(item["value"]) for item in values][::-1],
                    "values": [int(item["count"]) for item in values][::-1],
                    "value_label": "Rows",
                    "point_count": len(values),
                },
            ))
            break

    scatter = _scatter_spec(frame, insights)
    if scatter:
        visuals.append(_visual(
            "relationship", "Numeric relationship", "scatter",
            f"{scatter['y_field']} versus {scatter['x_field']}; highlighted points fall outside the typical range for at least one measure.",
            scatter,
        ))

    missing = [item for item in schema.get("columns") or [] if int(item.get("null_count") or 0) > 0]
    if missing:
        missing = sorted(missing, key=lambda item: float(item.get("null_rate") or 0), reverse=True)[:10]
        visuals.append(_visual(
            "data-quality", "Data quality", "bar",
            "Columns with the highest share of missing values.",
            {
                "field": "Column",
                "labels": [item["name"] for item in missing][::-1],
                "values": [round(float(item["null_rate"]) * 100, 2) for item in missing][::-1],
                "value_label": "Missing values (%)",
                "point_count": len(missing),
            },
        ))

    if len(numeric) >= 2:
        labels = numeric[:6]
        matrix = [
            [1.0 if left == right else (briefing._pearson(frame, left, right) or 0.0) for right in labels]
            for left in labels
        ]
        visuals.append(_visual(
            "correlation", "Numeric relationships", "heatmap",
            "Pairwise correlation across numeric columns; association does not establish causation.",
            {"labels": labels, "matrix": matrix, "point_count": len(labels) ** 2},
        ))

    outliers = [(column, int(count)) for column, count in (insights.get("outlier_counts") or {}).items() if int(count or 0) > 0]
    if outliers:
        outliers = sorted(outliers, key=lambda item: item[1], reverse=True)[:10]
        visuals.append(_visual(
            "outliers", "Exceptions worth review", "bar",
            "Rows outside the conventional 1.5× interquartile-range threshold.",
            {
                "field": "Column",
                "labels": [column for column, _ in outliers][::-1],
                "values": [count for _, count in outliers][::-1],
                "value_label": "Possible outliers",
                "point_count": len(outliers),
            },
        ))
    return visuals[:8]


def _payload(
    row: dict[str, Any],
    goal: str,
    report: dict[str, Any] | None,
    *,
    dataset_filename: str | None = None,
    dataset_size_bytes: int | None = None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    if report:
        schema, insights = report["schema"], report["insights"]
        briefing_payload, visuals = report["briefing"], report["visuals"]
    else:
        schema, insights, briefing_payload, visuals = {}, {}, briefing.empty_briefing(goal), []
    return {
        "session": {
            "id": row["session_id"],
            "name": row["session_name"],
            "dataset_filename": dataset_filename if dataset_filename is not None else row.get("dataset_filename"),
            "dataset_size_bytes": int(dataset_size_bytes if dataset_size_bytes is not None else row.get("dataset_size_bytes") or 0),
            "analysis_goal": goal,
            "created_at": row["created_at"],
            "updated_at": updated_at or row["updated_at"],
        },
        "schema": schema,
        "insights": insights,
        "briefing": briefing_payload,
        "visuals": visuals,
        "dashboard": {"revision": 3, "layout": "automatic_echarts_briefing", "analysis_goal": goal},
        "chart": visuals[0] if visuals else None,
    }


def build_echarts_report(frame, goal: str | None = None) -> dict[str, Any]:
    schema, insights = briefing.profile_frame(frame)
    return {
        "schema": schema,
        "insights": insights,
        "briefing": briefing.build_briefing(schema, insights, goal),
        "visuals": build_echarts_visuals(frame, schema, insights),
    }


def analysis_payload(session_id: str, owner: str | None) -> dict[str, Any]:
    """Return compact interactive chart specifications for the owning user."""
    row = analysis._row(session_id, owner)
    goal = briefing.get_goal(session_id, owner)
    report = build_echarts_report(analysis.read_csv(row["dataset_path"]), goal) if row.get("dataset_path") else None
    return _payload(row, goal, report)


def ingest_echarts(
    session_id: str,
    owner: str | None,
    dataset_path: str | os.PathLike[str],
    filename: str,
    byte_size: int,
) -> dict[str, Any]:
    """Persist analyses without constructing Plotly figures or image blobs."""
    row = analysis._row(session_id, owner)
    goal = briefing.get_goal(session_id, owner)
    report = build_echarts_report(analysis.read_csv(dataset_path), goal)
    dashboard = {"revision": 3, "layout": "automatic_echarts_briefing", "analysis_goal": goal}
    now = analysis._now()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE venture_analysis_sessions
            SET dataset_filename = :filename, dataset_path = :dataset_path,
                dataset_size_bytes = :byte_size, schema_json = :schema_json,
                insights_json = :insights_json, dashboard_json = :dashboard_json,
                updated_at = :updated_at
            WHERE session_id = :session_id AND owner = :owner
        """), {
            "filename": filename,
            "dataset_path": str(dataset_path),
            "byte_size": byte_size,
            "schema_json": analysis._dumps(report["schema"]),
            "insights_json": analysis._dumps(report["insights"]),
            "dashboard_json": analysis._dumps(dashboard),
            "updated_at": now,
            "session_id": session_id,
            "owner": analysis._owner(owner),
        })
    old_path = row.get("dataset_path")
    if old_path and old_path != str(dataset_path):
        try:
            old = Path(old_path).resolve()
            if analysis.DATASET_ROOT.resolve() in old.parents:
                old.unlink(missing_ok=True)
        except OSError:
            pass
    return _payload(
        row,
        goal,
        report,
        dataset_filename=filename,
        dataset_size_bytes=byte_size,
        updated_at=now,
    )
