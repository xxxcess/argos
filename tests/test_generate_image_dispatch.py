"""Tests for the agent generate_image interception path."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.generate_image_dispatch import install_generate_image_dispatch
from src.image_generation_defaults import ConfiguredImageEndpoint


def test_configured_image_default_bypasses_mcp_dispatch(monkeypatch):
    fallback_calls = []
    image_calls = []

    async def fallback(*args, **kwargs):
        fallback_calls.append((args, kwargs))
        return "fallback", {"exit_code": 0}

    tool_execution = SimpleNamespace(_execute_tool_block_impl=fallback)
    install_generate_image_dispatch(tool_execution)

    monkeypatch.setattr(
        "src.image_generation_defaults.resolve_configured_image_endpoint",
        lambda owner: ConfiguredImageEndpoint(
            endpoint_id="local-image",
            model="local-sd-turbo",
            base_url="http://127.0.0.1:7861/v1",
            headers={},
        ),
    )

    import src.ai_interaction as ai_interaction

    async def generate(content, session_id=None, owner=None):
        image_calls.append((content, session_id, owner))
        return {"results": "Generated image", "image_url": "/api/generated-image/cat.png"}

    monkeypatch.setattr(ai_interaction, "do_generate_image", generate)
    block = SimpleNamespace(tool_type="generate_image", content="a local cat")

    desc, result = asyncio.run(tool_execution._execute_tool_block_impl(block, session_id="s1", owner="alice"))

    assert desc == "generate_image: a local cat"
    assert result["exit_code"] == 0
    assert result["image_url"] == "/api/generated-image/cat.png"
    assert image_calls == [("a local cat", "s1", "alice")]
    assert fallback_calls == []


def test_unconfigured_image_tool_keeps_existing_mcp_path(monkeypatch):
    calls = []

    async def fallback(*args, **kwargs):
        calls.append((args, kwargs))
        return "fallback", {"exit_code": 0}

    tool_execution = SimpleNamespace(_execute_tool_block_impl=fallback)
    install_generate_image_dispatch(tool_execution)
    monkeypatch.setattr("src.image_generation_defaults.resolve_configured_image_endpoint", lambda owner: None)

    block = SimpleNamespace(tool_type="generate_image", content="a cloud cat")
    result = asyncio.run(tool_execution._execute_tool_block_impl(block, owner="alice"))

    assert result[0] == "fallback"
    assert len(calls) == 1
