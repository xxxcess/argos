from __future__ import annotations

import asyncio
import json

import src.agent_loop as al


def _collect(gen):
    async def _run():
        return [chunk async for chunk in gen]

    return asyncio.run(_run())


def _events(chunks):
    events = []
    for chunk in chunks:
        if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
            try:
                events.append(json.loads(chunk[6:]))
            except Exception:
                pass
    return events


def test_terminal_video_job_stops_before_followup_round(monkeypatch):
    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *a, **k: 10, raising=False)
    monkeypatch.setattr("src.video_agent_tool.is_video_agent_enabled", lambda owner: True)
    executed = []

    async def fake_stream(_candidates, _messages, **_kwargs):
        yield (
            'data: {"delta":"```generate_image\\nanchor first\\n```\\n'
            '```generate_video\\n{\\"prompt\\":\\"quiet portrait\\"}\\n```\\n'
            '```generate_video\\n{\\"prompt\\":\\"quiet portrait duplicate\\"}\\n```"}\n\n'
        )
        yield "data: [DONE]\n\n"

    async def fake_execute(block, *args, **kwargs):
        executed.append(block.tool_type)
        assert block.tool_type == "generate_video"
        return "generate_video", {
            "kind": "video_generation",
            "job_id": "job-abc",
            "status": "queued",
            "stage": "planning_anchor",
            "terminal_media_job": True,
            "assistant_ack": "Creating the image anchor, then rendering local depth-aware camera motion.",
            "output": "Creating the image anchor, then rendering local depth-aware camera motion.",
            "exit_code": 0,
            "video_job_id": "job-abc",
            "video_status": "queued",
            "video_stage": "planning_anchor",
        }

    monkeypatch.setattr(al, "stream_llm_with_fallback", fake_stream, raising=False)
    monkeypatch.setattr(al, "execute_tool_block", fake_execute, raising=False)

    chunks = _collect(
        al.stream_agent_loop(
            "http://x/v1",
            "m",
            [{"role": "user", "content": "create a video of a quiet portrait"}],
            max_rounds=3,
            relevant_tools={"generate_video"},
        )
    )
    events = _events(chunks)

    tool_outputs = [event for event in events if event.get("type") == "tool_output"]
    assert executed == ["generate_video"]
    assert len(tool_outputs) == 1
    assert tool_outputs[0]["kind"] == "video_generation"
    assert tool_outputs[0]["job_id"] == "job-abc"
    assert tool_outputs[0]["stage"] == "planning_anchor"
    assert tool_outputs[0]["terminal_media_job"] is True
    assert any(
        "Creating the image anchor, then rendering local depth-aware camera motion." in str(event.get("delta") or "")
        for event in events
    )
    assert not any(event.get("type") == "agent_step" for event in events)


def test_disabled_video_agent_omits_generate_video_schema(monkeypatch):
    captured = {}
    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *a, **k: 10, raising=False)
    monkeypatch.setattr("src.video_agent_tool.is_video_agent_enabled", lambda owner: False)

    async def fake_stream(_candidates, _messages, **kwargs):
        captured["tools"] = kwargs.get("tools") or []
        yield 'data: {"delta":"Video tool is unavailable."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", fake_stream, raising=False)

    _collect(
        al.stream_agent_loop(
            "https://api.openai.com/v1",
            "gpt-5",
            [{"role": "user", "content": "create a video"}],
            max_rounds=1,
            relevant_tools={"generate_video"},
        )
    )

    names = {
        (schema.get("function") or {}).get("name")
        for schema in captured["tools"]
        if schema.get("function")
    }
    assert "generate_video" not in names
