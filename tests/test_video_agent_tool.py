from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from src.agent_tools import ToolBlock
from src.tool_execution import execute_tool_block
from src.tool_parsing import parse_tool_blocks
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS, function_call_to_tool_block
from src.video_agent_tool import normalize_generate_video_request


class _FakeVideoService:
    def __init__(self) -> None:
        self.starts = 0
        self.created = []

    async def start(self) -> None:
        self.starts += 1

    def create_job(self, owner, body, *, allow_disabled=False):
        self.created.append((owner, body, allow_disabled))
        return SimpleNamespace(id="job-123", status="queued", stage="queued")


def _schema_names(disabled=()):
    blocked = set(disabled or ())
    return {
        (schema.get("function") or {}).get("name")
        for schema in FUNCTION_TOOL_SCHEMAS
        if (schema.get("function") or {}).get("name") not in blocked
    }


def test_generate_video_schema_is_filtered_by_disabled_preference():
    assert "generate_video" in _schema_names()
    assert "generate_video" not in _schema_names({"generate_video"})


def test_native_and_fenced_generate_video_normalize_to_same_request():
    native = function_call_to_tool_block(
        "generate_video",
        json.dumps({"prompt": "quiet portrait by a window", "seed": 17}),
    )
    fenced = parse_tool_blocks(
        '```generate_video\n{"prompt":"quiet portrait by a window","seed":17}\n```'
    )[0]

    assert native is not None
    assert normalize_generate_video_request(native.content) == normalize_generate_video_request(fenced.content)
    assert normalize_generate_video_request(native.content) == {
        "prompt": "quiet portrait by a window",
        "seed": 17,
    }


def test_disabled_generate_video_raw_block_is_rejected_before_dispatch(monkeypatch):
    service = _FakeVideoService()
    monkeypatch.setattr(
        "services.anchor_video_generation.get_video_generation_service",
        lambda: service,
    )

    desc, result = asyncio.run(
        execute_tool_block(
            ToolBlock("generate_video", '{"prompt":"make a video"}'),
            disabled_tools={"generate_video"},
            owner="alice",
        )
    )

    assert desc == "generate_video: BLOCKED"
    assert result["exit_code"] == 1
    assert result["error"] == "Generate video is disabled in Built-in Agent Tools."
    assert service.created == []


def test_generate_video_dispatch_creates_one_durable_job(monkeypatch):
    service = _FakeVideoService()
    image_calls = []
    monkeypatch.setattr(
        "services.anchor_video_generation.get_video_generation_service",
        lambda: service,
    )
    monkeypatch.setattr("src.video_agent_tool.is_video_agent_enabled", lambda owner: True)

    async def fake_image(*args, **kwargs):
        image_calls.append((args, kwargs))
        raise AssertionError("standalone image generation should not run during agent dispatch")

    monkeypatch.setattr("src.ai_interaction.do_generate_image", fake_image, raising=False)

    desc, result = asyncio.run(
        execute_tool_block(
            ToolBlock("generate_video", '{"prompt":"sunlit window portrait","seed":9}'),
            session_id="s1",
            owner="alice",
        )
    )

    assert desc == "generate_video"
    assert service.starts == 1
    assert service.created == [
        ("alice", {"prompt": "sunlit window portrait", "session_id": "s1", "video_seed": 9}, True)
    ]
    assert image_calls == []
    assert result["exit_code"] == 0
    assert result["kind"] == "video_generation"
    assert result["job_id"] == "job-123"
    assert result["stage"] == "planning_anchor"
    assert result["terminal_media_job"] is True
