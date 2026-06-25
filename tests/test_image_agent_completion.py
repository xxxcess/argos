"""Tests for terminal completion after successful agent image generation."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.image_agent_completion import install_image_agent_terminal


def test_successful_image_tool_ends_stream_before_followup_model_round():
    closed = []

    async def original_stream(*_args, **_kwargs):
        try:
            yield 'data: {"type":"tool_start","tool":"generate_image"}\n\n'
            yield (
                'data: {"type":"tool_output","tool":"generate_image",'
                '"exit_code":0,"image_url":"/api/generated-image/dragon.png"}\n\n'
            )
            # This event represents the provider failure from the otherwise
            # unnecessary post-tool LLM continuation. It must never reach UI.
            yield 'event: error\ndata: {"error":"500"}\n\n'
        finally:
            closed.append(True)

    module = SimpleNamespace(stream_agent_loop=original_stream)
    install_image_agent_terminal(module)

    async def collect():
        return [chunk async for chunk in module.stream_agent_loop("url", "model", [])]

    events = asyncio.run(collect())

    assert any('"tool_output"' in item for item in events)
    assert any('Image generated.' in item for item in events)
    assert any('"image_generation_complete": true' in item for item in events)
    assert events[-1] == 'data: [DONE]\n\n'
    assert not any('event: error' in item for item in events)
    assert closed == [True]


def test_failed_image_tool_does_not_short_circuit_agent():
    async def original_stream(*_args, **_kwargs):
        yield (
            'data: {"type":"tool_output","tool":"generate_image",'
            '"exit_code":1,"error":"generation failed"}\n\n'
        )
        yield 'data: {"delta":"Trying another route."}\n\n'
        yield 'data: [DONE]\n\n'

    module = SimpleNamespace(stream_agent_loop=original_stream)
    install_image_agent_terminal(module)

    async def collect():
        return [chunk async for chunk in module.stream_agent_loop("url", "model", [])]

    events = asyncio.run(collect())
    assert any('Trying another route.' in item for item in events)
    assert events[-1] == 'data: [DONE]\n\n'