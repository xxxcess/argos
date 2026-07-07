"""Decision-trail adapters for the ECharts Venture analysis payload."""
from __future__ import annotations

from typing import Any

from src.venture_analysis_decisions import decorate_payload
from src.venture_echarts_briefing import analysis_payload as _analysis_payload
from src.venture_echarts_briefing import ingest_echarts as _ingest_echarts


def analysis_payload(session_id: str, owner: str | None) -> dict[str, Any]:
    return decorate_payload(_analysis_payload(session_id, owner))


def ingest_echarts(
    session_id: str,
    owner: str | None,
    dataset_path: str,
    filename: str,
    byte_size: int,
) -> dict[str, Any]:
    return decorate_payload(_ingest_echarts(session_id, owner, dataset_path, filename, byte_size))
