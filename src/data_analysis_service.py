"""Owner-scoped CSV analysis sessions for the Argos workspace.

This module deliberately keeps dataset/dashboard persistence separate from regular
chat rows. Analysis sessions need immutable dataset provenance, a stateful
visualization configuration, and a compact conversation history rather than the
normal streamed-chat message shape.
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
from typing import Any, Iterable

from sqlalchemy import text

from core.database import engine
from src.constants import DATA_DIR


MAX_DATASET_BYTES = 200 * 1024 * 1024
MAX_MESSAGE_CHARS = 4_000
DATASET_ROOT = Path(DATA_DIR) / "analysis_datasets"


class DataAnalysisError(ValueError):
    """A user-visible validation or processing error for data-analysis routes."""


class AnalysisSessionNotFound(DataAnalysisError):
    """Raised when an analysis session is absent or belongs to another owner."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _owner_key(owner: str | None) -> str:
    return (owner or "").strip()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _json_loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        parsed = json.loads(value)
        return parsed if parsed is not None else fallback
    except (TypeError, ValueError):
        return fallback


def _safe_filename(value: str | None) -> str:
    name = Path(value or "dataset.csv").name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return (name or "dataset.csv")[:128]


def _safe_owner_segment(owner: str | None) -> str:
    # Do not expose user names as on-disk folder names. The UUID-derived suffix
    # keeps separate accounts from colliding after normalization.
    raw = _owner_key(owner) or "single-user"
    visible = re.sub(r"[^A-Za-z0-9_-]+", "_", raw)[:32].strip("_") or "user"
    suffix = uuid.uuid5(uuid.NAMESPACE_URL, raw).hex[:12]
    return f"{visible}-{suffix}"


def _require_libraries():
    try:
        import polars as pl
        import plotly.graph_objects as go
        import plotly.io as pio
    except ImportError as exc:  # pragma: no cover - depends on deployment install
        raise DataAnalysisError(
            "Data analysis dependencies are not installed. Install the project's "
            "requirements so Polars, Plotly, and Kaleido are available."
        ) from exc
    return pl, go, pio


def ensure_schema() -> None:
    """Create the isolated persistence schema idempotently at application startup."""
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS analysis_sessions (
                id TEXT PRIMARY KEY,
                owner TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL,
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
            CREATE TABLE IF NOT EXISTS analysis_messages (
                id TEXT PRIMARY KEY,
                analysis_session_id TEXT NOT NULL,
                owner TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                dashboard_state_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_analysis_sessions_owner_updated
            ON analysis_sessions(owner, updated_at)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_analysis_messages_session_created
            ON analysis_messages(analysis_session_id, created_at)
        """))


def _session_row(owner: str | None, session_id: str) -> dict[str, Any]:
    ensure_schema()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM analysis_sessions WHERE id = :id AND owner = :owner"),
            {"id": session_id, "owner": _owner_key(owner)},
        ).mappings().first()
    if not row:
        raise AnalysisSessionNotFound("Analysis session not found")
    return dict(row)


def create_analysis_session(owner: str | None, name: str | None = None) -> dict[str, Any]:
    ensure_schema()
    session_id = str(uuid.uuid4())
    now = _utcnow()
    cleaned_name = (name or "Data analysis").strip()[:120] or "Data analysis"
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO analysis_sessions
                    (id, owner, name, schema_json, insights_json, dashboard_json, created_at, updated_at)
                VALUES (:id, :owner, :name, '{}', '{}', '{}', :created_at, :updated_at)
            """),
            {
                "id": session_id,
                "owner": _owner_key(owner),
                "name": cleaned_name,
                "created_at": now,
                "updated_at": now,
            },
        )
    return _session_row(owner, session_id)


def list_analysis_sessions(owner: str | None) -> list[dict[str, Any]]:
    ensure_schema()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT id, name, dataset_filename, dataset_size_bytes, created_at, updated_at
                FROM analysis_sessions
                WHERE owner = :owner
                ORDER BY updated_at DESC, created_at DESC
            """),
            {"owner": _owner_key(owner)},
        ).mappings().all()
    return [dict(row) for row in rows]


def dataset_target(owner: str | None, session_id: str, filename: str | None) -> Path:
    """Return a controlled local target after proving session ownership."""
    _session_row(owner, session_id)
    name = _safe_filename(filename)
    if Path(name).suffix.lower() != ".csv":
        raise DataAnalysisError("Upload a CSV file (.csv)")
    target_dir = DATASET_ROOT / _safe_owner_segment(owner) / session_id
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / name


def _column_names(columns: Iterable[str]) -> list[str]:
    """Normalize CSV headers while preserving uniqueness and stable display labels."""
    used: dict[str, int] = {}
    normalized: list[str] = []
    for index, raw in enumerate(columns, start=1):
        candidate = re.sub(r"\s+", " ", str(raw or "").strip())
        candidate = candidate or f"column_{index}"
        count = used.get(candidate, 0) + 1
        used[candidate] = count
        normalized.append(candidate if count == 1 else f"{candidate}_{count}")
    return normalized


def _collect_lazy(frame):
    # Polars has used both `streaming=True` and `engine="streaming"` over recent
    # releases. Supporting both keeps the feature usable during ordinary upgrades.
    try:
        return frame.collect(engine="streaming")
    except TypeError:
        return frame.collect(streaming=True)


def load_clean_csv(dataset_path: str | os.PathLike[str]):
    """Read and normalize CSV data through Polars' lazy, multi-threaded path."""
    pl, _, _ = _require_libraries()
    path = Path(dataset_path)
    if not path.is_file():
        raise DataAnalysisError("The dataset file is no longer available")
    if path.stat().st_size > MAX_DATASET_BYTES:
        raise DataAnalysisError("The dataset exceeds the configured 200 MB limit")

    try:
        lazy = pl.scan_csv(
            path,
            infer_schema_length=10_000,
            null_values=["", "NA", "N/A", "NULL", "null", "None"],
            try_parse_dates=True,
        )
        frame = _collect_lazy(lazy)
    except Exception as exc:
        raise DataAnalysisError(f"Could not read this CSV: {exc}") from exc

    if not frame.columns:
        raise DataAnalysisError("The CSV does not contain any columns")

    normalized_columns = _column_names(frame.columns)
    if normalized_columns != frame.columns:
        frame = frame.rename(dict(zip(frame.columns, normalized_columns)))

    string_types = {getattr(pl, "String", None), getattr(pl, "Utf8", None)}
    string_types.discard(None)
    expressions = []
    for column, dtype in frame.schema.items():
        if dtype in string_types:
            trimmed = pl.col(column).str.strip_chars()
            expressions.append(
                pl.when(trimmed == "").then(None).otherwise(trimmed).alias(column)
            )
    if expressions:
        frame = frame.with_columns(expressions)
    return frame


def _is_numeric(dtype: Any) -> bool:
    numeric = getattr(dtype, "is_numeric", None)
    try:
        if callable(numeric):
            return bool(numeric())
    except Exception:
        pass
    return str(dtype).startswith(("Int", "UInt", "Float", "Decimal"))


def _safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        return value.item()
    except Exception:
        return str(value)


def _numeric_summary(series) -> dict[str, Any]:
    if series.len() == 0:
        return {"count": 0}
    summary: dict[str, Any] = {"count": int(series.len())}
    for label, fn in (("min", series.min), ("max", series.max), ("mean", series.mean), ("median", series.median)):
        try:
            value = fn()
            summary[label] = float(value) if value is not None else None
        except Exception:
            summary[label] = None
    return summary


def _top_values(frame, column: str, limit: int = 8) -> list[dict[str, Any]]:
    pl, _, _ = _require_libraries()
    try:
        counts = (
            frame.select(pl.col(column).cast(pl.String).fill_null("(missing)").alias(column))
            .group_by(column)
            .len()
            .sort("len", descending=True)
            .head(limit)
        )
        return [
            {"value": _safe_value(row.get(column)), "count": int(row.get("len") or 0)}
            for row in counts.to_dicts()
        ]
    except Exception:
        return []


def profile_frame(frame) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build compact, JSON-safe dataset schema and insight payloads."""
    columns: list[dict[str, Any]] = []
    numeric_columns: list[str] = []
    categorical_columns: list[str] = []
    null_total = 0
    row_count = int(frame.height)

    for column, dtype in frame.schema.items():
        null_count = int(frame.get_column(column).null_count())
        null_total += null_count
        numeric = _is_numeric(dtype)
        descriptor = {
            "name": column,
            "dtype": str(dtype),
            "null_count": null_count,
            "null_rate": round((null_count / row_count) if row_count else 0, 4),
            "numeric": numeric,
        }
        columns.append(descriptor)
        if numeric:
            numeric_columns.append(column)
        else:
            categorical_columns.append(column)

    numeric_stats: dict[str, Any] = {}
    outliers: dict[str, int] = {}
    for column in numeric_columns[:12]:
        series = frame.get_column(column).drop_nulls()
        numeric_stats[column] = _numeric_summary(series)
        try:
            if series.len() >= 4:
                q1 = series.quantile(0.25)
                q3 = series.quantile(0.75)
                if q1 is not None and q3 is not None:
                    spread = float(q3) - float(q1)
                    lower, upper = float(q1) - 1.5 * spread, float(q3) + 1.5 * spread
                    outliers[column] = int(((series < lower) | (series > upper)).sum())
        except Exception:
            continue

    top_categories = {
        column: _top_values(frame, column)
        for column in categorical_columns[:8]
    }
    schema = {"columns": columns, "row_count": row_count, "column_count": len(columns)}
    insights = {
        "overview": {
            "rows": row_count,
            "columns": len(columns),
            "missing_cells": null_total,
            "missing_rate": round(null_total / (row_count * len(columns)), 4) if row_count and columns else 0,
        },
        "numeric_stats": numeric_stats,
        "top_categories": top_categories,
        "outlier_counts": outliers,
        "sample_rows": [{key: _safe_value(value) for key, value in row.items()} for row in frame.head(25).to_dicts()],
    }
    return schema, insights


def default_dashboard_state(frame) -> dict[str, Any]:
    numeric = [column for column, dtype in frame.schema.items() if _is_numeric(dtype)]
    categorical = [column for column, dtype in frame.schema.items() if not _is_numeric(dtype)]
    if categorical:
        chart = {"kind": "bar", "column": categorical[0], "top_n": 12}
    elif numeric:
        chart = {"kind": "histogram", "column": numeric[0]}
    else:
        chart = {"kind": "table"}
    return {"revision": 1, "filters": [], "chart": chart}


def _resolve_column(frame, desired: str | None) -> str | None:
    if not desired:
        return None
    wanted = desired.strip().casefold()
    for column in frame.columns:
        if column.casefold() == wanted:
            return column
    for column in frame.columns:
        if wanted in column.casefold():
            return column
    return None


def _apply_filters(frame, filters: list[dict[str, str]]):
    pl, _, _ = _require_libraries()
    filtered = frame
    for item in filters or []:
        column = _resolve_column(filtered, str(item.get("column") or ""))
        if not column:
            continue
        value = str(item.get("value") or "")
        filtered = filtered.filter(pl.col(column).cast(pl.String) == value)
    return filtered


def _figure_payload(fig, chart: dict[str, Any]) -> dict[str, Any]:
    _, _, pio = _require_libraries()
    fig.update_layout(
        margin={"l": 44, "r": 20, "t": 56, "b": 52},
        height=430,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    try:
        figure_json = json.loads(pio.to_json(fig, validate=False))
    except Exception:
        figure_json = {}
    image_png_base64 = None
    image_error = None
    try:
        image_png_base64 = base64.b64encode(
            pio.to_image(fig, format="png", width=1000, height=500, scale=1)
        ).decode("ascii")
    except Exception as exc:  # Browser still receives Plotly JSON/table fallback.
        image_error = str(exc)
    return {
        "kind": chart.get("kind", "table"),
        "figure": figure_json,
        "image_png_base64": image_png_base64,
        "image_error": image_error,
        "summary": chart.get("summary", ""),
    }


def render_dashboard(frame, state: dict[str, Any]) -> dict[str, Any]:
    """Render the persisted dashboard state as Plotly JSON and a PNG when available."""
    _, go, _ = _require_libraries()
    state = state or {}
    chart = dict(state.get("chart") or {})
    kind = chart.get("kind") or "table"
    filtered = _apply_filters(frame, list(state.get("filters") or []))
    filter_note = f" after {len(state.get('filters') or [])} filter(s)" if state.get("filters") else ""

    if kind == "bar":
        column = _resolve_column(filtered, chart.get("column"))
        if not column:
            kind = "table"
        else:
            values = _top_values(filtered, column, int(chart.get("top_n") or 12))
            fig = go.Figure(go.Bar(
                x=[row["value"] for row in values],
                y=[row["count"] for row in values],
                hovertemplate="%{x}: %{y}<extra></extra>",
            ))
            fig.update_layout(title=f"Top {column} values{filter_note}", xaxis_title=column, yaxis_title="Rows")
            chart["summary"] = f"{len(values)} most frequent values for {column}{filter_note}."
            return _figure_payload(fig, {**chart, "kind": "bar"})

    if kind == "histogram":
        column = _resolve_column(filtered, chart.get("column"))
        if column and _is_numeric(filtered.schema[column]):
            values = filtered.get_column(column).drop_nulls().head(50_000).to_list()
            fig = go.Figure(go.Histogram(x=values, nbinsx=int(chart.get("bins") or 30)))
            fig.update_layout(title=f"Distribution of {column}{filter_note}", xaxis_title=column, yaxis_title="Rows")
            chart["summary"] = f"Distribution of {column}{filter_note}."
            return _figure_payload(fig, {**chart, "kind": "histogram"})
        kind = "table"

    if kind == "scatter":
        x = _resolve_column(filtered, chart.get("x"))
        y = _resolve_column(filtered, chart.get("y"))
        if x and y and _is_numeric(filtered.schema[x]) and _is_numeric(filtered.schema[y]):
            points = filtered.select([x, y]).drop_nulls().head(10_000)
            fig = go.Figure(go.Scattergl(
                x=points.get_column(x).to_list(),
                y=points.get_column(y).to_list(),
                mode="markers",
                marker={"size": 7, "opacity": 0.72},
            ))
            fig.update_layout(title=f"{y} vs {x}{filter_note}", xaxis_title=x, yaxis_title=y)
            chart["summary"] = f"Scatter plot of {y} against {x}{filter_note}."
            return _figure_payload(fig, {**chart, "kind": "scatter"})
        kind = "table"

    rows = [{key: _safe_value(value) for key, value in row.items()} for row in filtered.head(20).to_dicts()]
    headers = list(filtered.columns[:10])
    fig = go.Figure(go.Table(
        header={"values": headers},
        cells={"values": [[row.get(column) for row in rows] for column in headers]},
    ))
    fig.update_layout(title=f"Dataset preview{filter_note}")
    return _figure_payload(fig, {
        "kind": "table",
        "summary": f"Previewing {len(rows)} row(s){filter_note}.",
    })


def profile_dataset(dataset_path: str | os.PathLike[str]) -> dict[str, Any]:
    frame = load_clean_csv(dataset_path)
    schema, insights = profile_frame(frame)
    dashboard = default_dashboard_state(frame)
    chart = render_dashboard(frame, dashboard)
    return {"schema": schema, "insights": insights, "dashboard": dashboard, "chart": chart}


def _update_session_dataset(
    owner: str | None,
    session_id: str,
    filename: str,
    dataset_path: str,
    byte_size: int,
    profile: dict[str, Any],
) -> None:
    now = _utcnow()
    with engine.begin() as conn:
        result = conn.execute(
            text("""
                UPDATE analysis_sessions
                SET dataset_filename = :filename,
                    dataset_path = :dataset_path,
                    dataset_size_bytes = :byte_size,
                    schema_json = :schema_json,
                    insights_json = :insights_json,
                    dashboard_json = :dashboard_json,
                    updated_at = :updated_at
                WHERE id = :id AND owner = :owner
            """),
            {
                "filename": filename,
                "dataset_path": dataset_path,
                "byte_size": byte_size,
                "schema_json": _json_dumps(profile["schema"]),
                "insights_json": _json_dumps(profile["insights"]),
                "dashboard_json": _json_dumps(profile["dashboard"]),
                "updated_at": now,
                "id": session_id,
                "owner": _owner_key(owner),
            },
        )
    if not result.rowcount:
        raise AnalysisSessionNotFound("Analysis session not found")


def ingest_dataset(
    owner: str | None,
    session_id: str,
    dataset_path: str | os.PathLike[str],
    filename: str,
    byte_size: int,
) -> dict[str, Any]:
    """Profile a persisted CSV and store its compact dashboard state in the DB."""
    _session_row(owner, session_id)
    profile = profile_dataset(dataset_path)
    _update_session_dataset(owner, session_id, filename, str(dataset_path), byte_size, profile)
    return build_session_payload(owner, session_id, profile_override=profile)


def _messages(owner: str | None, session_id: str) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT id, role, content, dashboard_state_json, created_at
                FROM analysis_messages
                WHERE analysis_session_id = :session_id AND owner = :owner
                ORDER BY created_at ASC, id ASC
            """),
            {"session_id": session_id, "owner": _owner_key(owner)},
        ).mappings().all()
    return [
        {
            "id": row["id"],
            "role": row["role"],
            "content": row["content"],
            "dashboard_state": _json_loads(row["dashboard_state_json"], {}),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def build_session_payload(
    owner: str | None,
    session_id: str,
    *,
    profile_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = _session_row(owner, session_id)
    schema = _json_loads(row.get("schema_json"), {})
    insights = _json_loads(row.get("insights_json"), {})
    dashboard = _json_loads(row.get("dashboard_json"), {})
    chart = None

    if profile_override:
        schema = profile_override["schema"]
        insights = profile_override["insights"]
        dashboard = profile_override["dashboard"]
        chart = profile_override["chart"]
    elif row.get("dataset_path"):
        frame = load_clean_csv(row["dataset_path"])
        if not dashboard:
            dashboard = default_dashboard_state(frame)
        chart = render_dashboard(frame, dashboard)

    return {
        "session": {
            "id": row["id"],
            "name": row["name"],
            "dataset_filename": row.get("dataset_filename"),
            "dataset_size_bytes": int(row.get("dataset_size_bytes") or 0),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        },
        "schema": schema,
        "insights": insights,
        "dashboard": dashboard,
        "chart": chart,
        "messages": _messages(owner, session_id),
    }


def _persist_message(
    owner: str | None,
    session_id: str,
    role: str,
    content: str,
    dashboard_state: dict[str, Any],
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO analysis_messages
                    (id, analysis_session_id, owner, role, content, dashboard_state_json, created_at)
                VALUES (:id, :session_id, :owner, :role, :content, :dashboard_state_json, :created_at)
            """),
            {
                "id": str(uuid.uuid4()),
                "session_id": session_id,
                "owner": _owner_key(owner),
                "role": role,
                "content": content,
                "dashboard_state_json": _json_dumps(dashboard_state),
                "created_at": _utcnow(),
            },
        )


def _update_dashboard(owner: str | None, session_id: str, dashboard: dict[str, Any]) -> None:
    with engine.begin() as conn:
        result = conn.execute(
            text("""
                UPDATE analysis_sessions
                SET dashboard_json = :dashboard_json, updated_at = :updated_at
                WHERE id = :id AND owner = :owner
            """),
            {
                "dashboard_json": _json_dumps(dashboard),
                "updated_at": _utcnow(),
                "id": session_id,
                "owner": _owner_key(owner),
            },
        )
    if not result.rowcount:
        raise AnalysisSessionNotFound("Analysis session not found")


def _command_dashboard(frame, previous: dict[str, Any], prompt: str) -> tuple[dict[str, Any], str]:
    """Apply safe, deterministic dashboard commands without executing user code."""
    state = {
        "revision": int(previous.get("revision") or 0) + 1,
        "filters": list(previous.get("filters") or []),
        "chart": dict(previous.get("chart") or default_dashboard_state(frame)["chart"]),
    }
    lowered = prompt.casefold().strip()

    if lowered in {"reset", "reset dashboard", "clear filters"}:
        reset = default_dashboard_state(frame)
        reset["revision"] = state["revision"]
        return reset, "Reset the dashboard to its default view."

    filter_match = re.search(
        r"(?:filter|where)\s+(.+?)\s*(?:=|equals|is)\s*[\"']?(.+?)[\"']?\s*$",
        prompt,
        flags=re.IGNORECASE,
    )
    if filter_match:
        column = _resolve_column(frame, filter_match.group(1))
        if column:
            value = filter_match.group(2).strip()
            state["filters"] = [item for item in state["filters"] if item.get("column") != column]
            state["filters"].append({"column": column, "value": value})
            return state, f"Filtered `{column}` to `{value}`."
        return state, f"I could not find a column matching `{filter_match.group(1).strip()}`."

    scatter_match = re.search(r"scatter(?:\s+plot)?\s+(.+?)\s+(?:vs|versus|against)\s+(.+)$", prompt, flags=re.IGNORECASE)
    if scatter_match:
        x = _resolve_column(frame, scatter_match.group(1))
        y = _resolve_column(frame, scatter_match.group(2))
        if x and y and _is_numeric(frame.schema[x]) and _is_numeric(frame.schema[y]):
            state["chart"] = {"kind": "scatter", "x": x, "y": y}
            return state, f"Switched to a scatter plot of `{y}` versus `{x}`."
        return state, "A scatter plot needs two numeric columns."

    histogram_match = re.search(r"(?:histogram|distribution)\s+(?:of\s+)?(.+)$", prompt, flags=re.IGNORECASE)
    if histogram_match:
        column = _resolve_column(frame, histogram_match.group(1))
        if column and _is_numeric(frame.schema[column]):
            state["chart"] = {"kind": "histogram", "column": column}
            return state, f"Switched to a histogram of `{column}`."
        return state, "A histogram needs a numeric column."

    top_match = re.search(r"(?:bar(?:\s+chart)?|top)\s+(?:(\d+)\s+)?(?:of\s+)?(.+)$", prompt, flags=re.IGNORECASE)
    if top_match:
        top_n = int(top_match.group(1) or 12)
        column = _resolve_column(frame, top_match.group(2))
        if column:
            state["chart"] = {"kind": "bar", "column": column, "top_n": max(2, min(top_n, 50))}
            return state, f"Showing the most frequent `{column}` values."
        return state, f"I could not find a column matching `{top_match.group(2).strip()}`."

    if "table" in lowered or "preview" in lowered or "rows" in lowered:
        state["chart"] = {"kind": "table"}
        return state, "Showing a filtered dataset preview."

    return (
        state,
        "The dashboard state is saved. Try `histogram revenue`, `scatter price vs quantity`, "
        "`top 10 region`, `filter region = East`, `preview`, or `reset`.",
    )


def post_analysis_message(owner: str | None, session_id: str, content: str) -> dict[str, Any]:
    row = _session_row(owner, session_id)
    prompt = (content or "").strip()
    if not prompt:
        raise DataAnalysisError("Enter a dashboard question or command")
    if len(prompt) > MAX_MESSAGE_CHARS:
        raise DataAnalysisError(f"Messages are limited to {MAX_MESSAGE_CHARS:,} characters")
    if not row.get("dataset_path"):
        raise DataAnalysisError("Upload a CSV before asking the analysis dashboard")

    frame = load_clean_csv(row["dataset_path"])
    previous = _json_loads(row.get("dashboard_json"), {}) or default_dashboard_state(frame)
    dashboard, response = _command_dashboard(frame, previous, prompt)
    _persist_message(owner, session_id, "user", prompt, dashboard)
    _update_dashboard(owner, session_id, dashboard)
    chart = render_dashboard(frame, dashboard)
    _persist_message(owner, session_id, "assistant", response, dashboard)
    payload = build_session_payload(owner, session_id)
    payload["chart"] = chart
    return payload


def delete_analysis_session(owner: str | None, session_id: str) -> None:
    row = _session_row(owner, session_id)
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM analysis_messages WHERE analysis_session_id = :session_id AND owner = :owner"),
            {"session_id": session_id, "owner": _owner_key(owner)},
        )
        result = conn.execute(
            text("DELETE FROM analysis_sessions WHERE id = :id AND owner = :owner"),
            {"id": session_id, "owner": _owner_key(owner)},
        )
    if not result.rowcount:
        raise AnalysisSessionNotFound("Analysis session not found")

    dataset_path = row.get("dataset_path")
    if dataset_path:
        try:
            path = Path(dataset_path).resolve()
            root = DATASET_ROOT.resolve()
            if root in path.parents:
                shutil.rmtree(path.parent, ignore_errors=True)
        except OSError:
            pass
