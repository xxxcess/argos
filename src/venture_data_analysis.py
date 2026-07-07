"""Stateful CSV analysis for Argos Venture workspace sessions.

Analysis sessions deliberately use a dedicated persistence shape instead of
``chat_messages``: a dashboard needs dataset provenance, a mutable view state,
and a concise interaction log. The normal session gets one system timeline entry
only so the workspace can restore and list the tab after a restart.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

from core.database import Session as DbSession, SessionLocal, engine
from src.constants import DATA_DIR


MAX_DATASET_BYTES = 200 * 1024 * 1024
MAX_PROMPT_CHARS = 4_000
DATASET_ROOT = Path(DATA_DIR) / "venture_analysis_datasets"


class AnalysisError(ValueError):
    """A safe, user-facing error from the analysis pipeline."""


class AnalysisNotFound(AnalysisError):
    """Raised for unavailable or cross-owner analysis sessions."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _owner(owner: str | None) -> str:
    return (owner or "").strip()


def _loads(value: str | None, fallback: Any):
    try:
        parsed = json.loads(value or "")
        return fallback if parsed is None else parsed
    except (TypeError, ValueError):
        return fallback


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _safe_filename(value: str | None) -> str:
    name = Path(value or "dataset.csv").name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    name = (name or "dataset.csv")[:128]
    if Path(name).suffix.lower() != ".csv":
        raise AnalysisError("Upload a CSV file (.csv)")
    return name


def _owner_segment(owner: str | None) -> str:
    # Avoid using a raw account name as a dataset directory while retaining a
    # deterministic, collision-resistant partition between users.
    raw = _owner(owner) or "single-user"
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", raw).strip("_")[:28] or "user"
    return f"{safe}-{uuid.uuid5(uuid.NAMESPACE_URL, raw).hex[:12]}"


def _require_libraries():
    try:
        import polars as pl
        import plotly.graph_objects as go
        import plotly.io as pio
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise AnalysisError(
            "Data Analysis requires Polars, Plotly, and Kaleido. "
            "Install the project requirements and restart Argos Venture."
        ) from exc
    return pl, go, pio


def ensure_schema() -> None:
    """Create independent analysis tables safely for existing Venture installs."""
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS venture_analysis_sessions (
                session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
                owner TEXT NOT NULL DEFAULT '',
                dataset_filename TEXT,
                dataset_path TEXT,
                dataset_size_bytes INTEGER NOT NULL DEFAULT 0,
                schema_json TEXT NOT NULL DEFAULT '{}',
                insights_json TEXT NOT NULL DEFAULT '{}',
                dashboard_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS venture_analysis_messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                owner TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                dashboard_state_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_venture_analysis_owner_updated
            ON venture_analysis_sessions(owner, updated_at)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_venture_analysis_message_session_created
            ON venture_analysis_messages(session_id, created_at)
        """))


def register_analysis_session(session_id: str, owner: str | None) -> None:
    ensure_schema()
    now = _now()
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO venture_analysis_sessions
              (session_id, owner, schema_json, insights_json, dashboard_json, created_at, updated_at)
            VALUES (:session_id, :owner, '{}', '{}', '{}', :created_at, :updated_at)
            ON CONFLICT(session_id) DO NOTHING
        """), {
            "session_id": session_id,
            "owner": _owner(owner),
            "created_at": now,
            "updated_at": now,
        })


def _row(session_id: str, owner: str | None) -> dict[str, Any]:
    ensure_schema()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT analysis.*, sessions.name AS session_name
            FROM venture_analysis_sessions AS analysis
            JOIN sessions ON sessions.id = analysis.session_id
            WHERE analysis.session_id = :session_id
              AND analysis.owner = :owner
              AND sessions.owner = :owner
              AND sessions.mode = 'analysis'
              AND sessions.archived = 0
        """), {"session_id": session_id, "owner": _owner(owner)}).mappings().first()
    if not row:
        raise AnalysisNotFound("Data analysis session not found")
    return dict(row)


def is_analysis_session(session_id: str, owner: str | None) -> bool:
    try:
        _row(session_id, owner)
        return True
    except AnalysisError:
        return False


def session_target(session_id: str, owner: str | None, filename: str | None) -> Path:
    _row(session_id, owner)
    target_dir = DATASET_ROOT / _owner_segment(owner) / session_id
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / _safe_filename(filename)


def _collect(lazy):
    try:
        return lazy.collect(engine="streaming")
    except TypeError:  # Polars versions before the engine keyword
        return lazy.collect(streaming=True)


def read_csv(dataset_path: str | os.PathLike[str]):
    """Read and clean CSV input through Polars' lazy multi-threaded engine."""
    pl, _, _ = _require_libraries()
    path = Path(dataset_path)
    if not path.is_file():
        raise AnalysisError("The uploaded dataset is no longer available")
    if path.stat().st_size > MAX_DATASET_BYTES:
        raise AnalysisError("The dataset exceeds the 200 MB upload limit")
    try:
        lazy = pl.scan_csv(
            path,
            infer_schema_length=10_000,
            null_values=["", "NA", "N/A", "NULL", "null", "None"],
            try_parse_dates=True,
        )
        frame = _collect(lazy)
    except Exception as exc:
        raise AnalysisError(f"Could not read this CSV: {exc}") from exc

    if not frame.columns:
        raise AnalysisError("The CSV does not contain any columns")

    # Normalize blank/duplicate headers so later prompt commands can resolve a
    # stable column name instead of relying on positional indexes.
    used: dict[str, int] = {}
    names: list[str] = []
    for index, column in enumerate(frame.columns, start=1):
        base = re.sub(r"\s+", " ", str(column or "").strip()) or f"column_{index}"
        used[base] = used.get(base, 0) + 1
        names.append(base if used[base] == 1 else f"{base}_{used[base]}")
    if names != frame.columns:
        frame = frame.rename(dict(zip(frame.columns, names)))

    string_dtypes = {getattr(pl, "String", None), getattr(pl, "Utf8", None)} - {None}
    trims = []
    for column, dtype in frame.schema.items():
        if dtype in string_dtypes:
            trimmed = pl.col(column).str.strip_chars()
            trims.append(pl.when(trimmed == "").then(None).otherwise(trimmed).alias(column))
    return frame.with_columns(trims) if trims else frame


def _numeric(dtype: Any) -> bool:
    checker = getattr(dtype, "is_numeric", None)
    try:
        if callable(checker):
            return bool(checker())
    except Exception:
        pass
    return str(dtype).startswith(("Int", "UInt", "Float", "Decimal"))


def _safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        return value.item()
    except Exception:
        return str(value)


def _summary(series) -> dict[str, Any]:
    result = {"count": int(series.len())}
    if not series.len():
        return result
    for label, func in (("min", series.min), ("max", series.max), ("mean", series.mean), ("median", series.median)):
        try:
            value = func()
            result[label] = float(value) if value is not None else None
        except Exception:
            result[label] = None
    return result


def _top_values(frame, column: str, limit: int = 10) -> list[dict[str, Any]]:
    pl, _, _ = _require_libraries()
    try:
        values = (frame
            .select(pl.col(column).cast(pl.String).fill_null("(missing)").alias(column))
            .group_by(column).len().sort("len", descending=True).head(limit))
        return [{"value": _safe(item.get(column)), "count": int(item.get("len") or 0)} for item in values.to_dicts()]
    except Exception:
        return []


def profile(frame) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = int(frame.height)
    columns: list[dict[str, Any]] = []
    numeric: list[str] = []
    categorical: list[str] = []
    missing = 0
    for column, dtype in frame.schema.items():
        nulls = int(frame.get_column(column).null_count())
        missing += nulls
        is_numeric = _numeric(dtype)
        columns.append({
            "name": column,
            "dtype": str(dtype),
            "numeric": is_numeric,
            "null_count": nulls,
            "null_rate": round(nulls / rows, 4) if rows else 0,
        })
        (numeric if is_numeric else categorical).append(column)

    numeric_stats: dict[str, Any] = {}
    outliers: dict[str, int] = {}
    for column in numeric[:12]:
        values = frame.get_column(column).drop_nulls()
        numeric_stats[column] = _summary(values)
        try:
            if values.len() >= 4:
                q1, q3 = values.quantile(0.25), values.quantile(0.75)
                if q1 is not None and q3 is not None:
                    spread = float(q3) - float(q1)
                    if spread >= 0:
                        outliers[column] = int(((values < float(q1) - 1.5 * spread) | (values > float(q3) + 1.5 * spread)).sum())
        except Exception:
            continue

    schema = {"row_count": rows, "column_count": len(columns), "columns": columns}
    insights = {
        "overview": {
            "rows": rows,
            "columns": len(columns),
            "missing_cells": missing,
            "missing_rate": round(missing / (rows * len(columns)), 4) if rows and columns else 0,
        },
        "numeric_stats": numeric_stats,
        "outlier_counts": outliers,
        "top_categories": {column: _top_values(frame, column) for column in categorical[:8]},
        "sample_rows": [{key: _safe(value) for key, value in row.items()} for row in frame.head(25).to_dicts()],
    }
    return schema, insights


def default_dashboard(frame) -> dict[str, Any]:
    numeric = [column for column, dtype in frame.schema.items() if _numeric(dtype)]
    categorical = [column for column, dtype in frame.schema.items() if not _numeric(dtype)]
    chart = ({"kind": "bar", "column": categorical[0], "top_n": 12} if categorical
             else {"kind": "histogram", "column": numeric[0]} if numeric
             else {"kind": "table"})
    return {"revision": 1, "filters": [], "chart": chart}


def _column(frame, value: str | None) -> str | None:
    target = str(value or "").strip().casefold()
    if not target:
        return None
    for column in frame.columns:
        if column.casefold() == target:
            return column
    for column in frame.columns:
        if target in column.casefold():
            return column
    return None


def _filtered(frame, filters: list[dict[str, str]]):
    pl, _, _ = _require_libraries()
    result = frame
    for item in filters or []:
        column = _column(result, item.get("column"))
        if column:
            result = result.filter(pl.col(column).cast(pl.String) == str(item.get("value") or ""))
    return result


def _chart_response(figure, kind: str, summary: str) -> dict[str, Any]:
    _, _, pio = _require_libraries()
    figure.update_layout(
        margin={"l": 44, "r": 20, "t": 55, "b": 52},
        height=420,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    try:
        figure_json = json.loads(pio.to_json(figure, validate=False))
    except Exception:
        figure_json = {}
    image = None
    image_error = None
    try:
        image = base64.b64encode(pio.to_image(figure, format="png", width=1000, height=500, scale=1)).decode("ascii")
    except Exception as exc:
        image_error = str(exc)
    return {
        "kind": kind,
        "summary": summary,
        "figure": figure_json,
        "image_png_base64": image,
        "image_error": image_error,
    }


def render(frame, dashboard: dict[str, Any]) -> dict[str, Any]:
    _, go, _ = _require_libraries()
    state = dashboard or {}
    chart = dict(state.get("chart") or {})
    filtered = _filtered(frame, list(state.get("filters") or []))
    suffix = f" after {len(state.get('filters') or [])} filter(s)" if state.get("filters") else ""
    kind = chart.get("kind", "table")

    if kind == "bar":
        column = _column(filtered, chart.get("column"))
        if column:
            values = _top_values(filtered, column, max(2, min(int(chart.get("top_n") or 12), 50)))
            fig = go.Figure(go.Bar(x=[row["value"] for row in values], y=[row["count"] for row in values]))
            fig.update_layout(title=f"Top {column} values{suffix}", xaxis_title=column, yaxis_title="Rows")
            return _chart_response(fig, "bar", f"Most frequent {column} values{suffix}.")

    if kind == "histogram":
        column = _column(filtered, chart.get("column"))
        if column and _numeric(filtered.schema[column]):
            fig = go.Figure(go.Histogram(x=filtered.get_column(column).drop_nulls().head(50_000).to_list(), nbinsx=int(chart.get("bins") or 30)))
            fig.update_layout(title=f"Distribution of {column}{suffix}", xaxis_title=column, yaxis_title="Rows")
            return _chart_response(fig, "histogram", f"Distribution of {column}{suffix}.")

    if kind == "scatter":
        x, y = _column(filtered, chart.get("x")), _column(filtered, chart.get("y"))
        if x and y and _numeric(filtered.schema[x]) and _numeric(filtered.schema[y]):
            points = filtered.select([x, y]).drop_nulls().head(10_000)
            fig = go.Figure(go.Scattergl(x=points.get_column(x).to_list(), y=points.get_column(y).to_list(), mode="markers", marker={"size": 7, "opacity": 0.72}))
            fig.update_layout(title=f"{y} vs {x}{suffix}", xaxis_title=x, yaxis_title=y)
            return _chart_response(fig, "scatter", f"Scatter plot of {y} versus {x}{suffix}.")

    rows = [{key: _safe(value) for key, value in row.items()} for row in filtered.head(20).to_dicts()]
    headers = list(filtered.columns[:10])
    fig = go.Figure(go.Table(header={"values": headers}, cells={"values": [[row.get(column) for row in rows] for column in headers]}))
    fig.update_layout(title=f"Dataset preview{suffix}")
    return _chart_response(fig, "table", f"Previewing {len(rows)} row(s){suffix}.")


def _messages(session_id: str, owner: str | None) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, role, content, dashboard_state_json, created_at
            FROM venture_analysis_messages
            WHERE session_id = :session_id AND owner = :owner
            ORDER BY created_at, id
        """), {"session_id": session_id, "owner": _owner(owner)}).mappings().all()
    return [{
        "id": row["id"], "role": row["role"], "content": row["content"],
        "dashboard_state": _loads(row["dashboard_state_json"], {}), "created_at": row["created_at"],
    } for row in rows]


def payload(session_id: str, owner: str | None, *, profile_result: dict[str, Any] | None = None) -> dict[str, Any]:
    row = _row(session_id, owner)
    schema = _loads(row.get("schema_json"), {})
    insights = _loads(row.get("insights_json"), {})
    dashboard = _loads(row.get("dashboard_json"), {})
    chart = None
    if profile_result:
        schema = profile_result["schema"]
        insights = profile_result["insights"]
        dashboard = profile_result["dashboard"]
        chart = profile_result["chart"]
    elif row.get("dataset_path"):
        frame = read_csv(row["dataset_path"])
        dashboard = dashboard or default_dashboard(frame)
        chart = render(frame, dashboard)
    return {
        "session": {
            "id": row["session_id"], "name": row["session_name"],
            "dataset_filename": row.get("dataset_filename"),
            "dataset_size_bytes": int(row.get("dataset_size_bytes") or 0),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        },
        "schema": schema, "insights": insights, "dashboard": dashboard,
        "chart": chart, "messages": _messages(session_id, owner),
    }


def ingest(session_id: str, owner: str | None, dataset_path: str | os.PathLike[str], filename: str, byte_size: int) -> dict[str, Any]:
    row = _row(session_id, owner)
    frame = read_csv(dataset_path)
    schema, insights = profile(frame)
    dashboard = default_dashboard(frame)
    result = {"schema": schema, "insights": insights, "dashboard": dashboard, "chart": render(frame, dashboard)}
    now = _now()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE venture_analysis_sessions
            SET dataset_filename = :filename, dataset_path = :dataset_path,
                dataset_size_bytes = :byte_size, schema_json = :schema_json,
                insights_json = :insights_json, dashboard_json = :dashboard_json,
                updated_at = :updated_at
            WHERE session_id = :session_id AND owner = :owner
        """), {
            "filename": filename, "dataset_path": str(dataset_path), "byte_size": byte_size,
            "schema_json": _dumps(schema), "insights_json": _dumps(insights),
            "dashboard_json": _dumps(dashboard), "updated_at": now,
            "session_id": session_id, "owner": _owner(owner),
        })
    old_path = row.get("dataset_path")
    if old_path and old_path != str(dataset_path):
        try:
            old = Path(old_path).resolve()
            if DATASET_ROOT.resolve() in old.parents:
                old.unlink(missing_ok=True)
        except OSError:
            pass
    return payload(session_id, owner, profile_result=result)


def _save_message(session_id: str, owner: str | None, role: str, content: str, dashboard: dict[str, Any]) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO venture_analysis_messages
                (id, session_id, owner, role, content, dashboard_state_json, created_at)
            VALUES (:id, :session_id, :owner, :role, :content, :dashboard_state_json, :created_at)
        """), {
            "id": uuid.uuid4().hex, "session_id": session_id, "owner": _owner(owner),
            "role": role, "content": content, "dashboard_state_json": _dumps(dashboard), "created_at": _now(),
        })


def _save_dashboard(session_id: str, owner: str | None, dashboard: dict[str, Any]) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE venture_analysis_sessions
            SET dashboard_json = :dashboard_json, updated_at = :updated_at
            WHERE session_id = :session_id AND owner = :owner
        """), {
            "dashboard_json": _dumps(dashboard), "updated_at": _now(),
            "session_id": session_id, "owner": _owner(owner),
        })


def _apply_prompt(frame, current: dict[str, Any], prompt: str) -> tuple[dict[str, Any], str]:
    """Interpret a safe, inspectable dashboard-command subset; never execute code."""
    state = {
        "revision": int(current.get("revision") or 0) + 1,
        "filters": list(current.get("filters") or []),
        "chart": dict(current.get("chart") or default_dashboard(frame)["chart"]),
    }
    stripped = prompt.strip()
    lowered = stripped.casefold()
    if lowered in {"reset", "reset dashboard", "clear filters"}:
        reset = default_dashboard(frame)
        reset["revision"] = state["revision"]
        return reset, "Reset the dashboard to the default view."

    match = re.search(r"(?:filter|where)\s+(.+?)\s*(?:=|equals|is)\s*[\"']?(.+?)[\"']?\s*$", stripped, flags=re.IGNORECASE)
    if match:
        column = _column(frame, match.group(1))
        if not column:
            return state, f"I could not find a column matching `{match.group(1).strip()}`."
        value = match.group(2).strip()
        state["filters"] = [item for item in state["filters"] if item.get("column") != column]
        state["filters"].append({"column": column, "value": value})
        return state, f"Filtered `{column}` to `{value}`."

    match = re.search(r"scatter(?:\s+plot)?\s+(.+?)\s+(?:vs|versus|against)\s+(.+)$", stripped, flags=re.IGNORECASE)
    if match:
        x, y = _column(frame, match.group(1)), _column(frame, match.group(2))
        if x and y and _numeric(frame.schema[x]) and _numeric(frame.schema[y]):
            state["chart"] = {"kind": "scatter", "x": x, "y": y}
            return state, f"Switched to a scatter plot of `{y}` versus `{x}`."
        return state, "A scatter plot needs two numeric columns."

    match = re.search(r"(?:histogram|distribution)\s+(?:of\s+)?(.+)$", stripped, flags=re.IGNORECASE)
    if match:
        column = _column(frame, match.group(1))
        if column and _numeric(frame.schema[column]):
            state["chart"] = {"kind": "histogram", "column": column}
            return state, f"Switched to a histogram of `{column}`."
        return state, "A histogram needs a numeric column."

    match = re.search(r"(?:bar(?:\s+chart)?|top)\s+(?:(\d+)\s+)?(?:of\s+)?(.+)$", stripped, flags=re.IGNORECASE)
    if match:
        column = _column(frame, match.group(2))
        if column:
            state["chart"] = {"kind": "bar", "column": column, "top_n": max(2, min(int(match.group(1) or 12), 50))}
            return state, f"Showing the most frequent `{column}` values."
        return state, f"I could not find a column matching `{match.group(2).strip()}`."

    if any(word in lowered for word in ("preview", "table", "rows")):
        state["chart"] = {"kind": "table"}
        return state, "Showing a filtered dataset preview."

    return state, (
        "Dashboard state is saved. Try `histogram revenue`, `scatter price vs quantity`, "
        "`top 10 region`, `filter region = East`, `preview`, or `reset`."
    )


def message(session_id: str, owner: str | None, content: str) -> dict[str, Any]:
    row = _row(session_id, owner)
    prompt = (content or "").strip()
    if not prompt:
        raise AnalysisError("Enter a dashboard question or command")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise AnalysisError(f"Messages are limited to {MAX_PROMPT_CHARS:,} characters")
    if not row.get("dataset_path"):
        raise AnalysisError("Upload a CSV before using the analysis dashboard")
    frame = read_csv(row["dataset_path"])
    current = _loads(row.get("dashboard_json"), {}) or default_dashboard(frame)
    dashboard, response = _apply_prompt(frame, current, prompt)
    _save_message(session_id, owner, "user", prompt, dashboard)
    _save_dashboard(session_id, owner, dashboard)
    chart = render(frame, dashboard)
    _save_message(session_id, owner, "assistant", response, dashboard)
    result = payload(session_id, owner)
    result["chart"] = chart
    return result


def cleanup_dataset_for_session(session_id: str, owner: str | None) -> None:
    """Best-effort disk cleanup for callers that delete an analysis session."""
    try:
        row = _row(session_id, owner)
    except AnalysisError:
        return
    path = row.get("dataset_path")
    if not path:
        return
    try:
        target = Path(path).resolve()
        if DATASET_ROOT.resolve() in target.parents:
            shutil.rmtree(target.parent, ignore_errors=True)
    except OSError:
        pass
