"""Tests for request-local image-generation default wiring."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.image_generation_defaults import (
    ConfiguredImageEndpoint,
    _cap_local_size,
    _replace_tool_model,
    install_image_generation_defaults,
)


def test_replace_tool_model_preserves_prompt_size_and_quality():
    assert _replace_tool_model("cat\ngpt-image-1\n1024x1024\nhigh", "local-sd-turbo") == (
        "cat\nlocal-sd-turbo\n1024x1024\nhigh"
    )
    assert _replace_tool_model("cat", "local-sd-turbo") == "cat\nlocal-sd-turbo"


def test_cap_local_size_preserves_small_valid_requests_and_clamps_defaults():
    assert _cap_local_size("cat\nlocal-sd-turbo\n512x384\nmedium") == "cat\nlocal-sd-turbo\n512x384\nmedium"
    assert _cap_local_size("cat\nlocal-sd-turbo\n1024x1024\nhigh") == "cat\nlocal-sd-turbo\n512x512\nhigh"
    assert _cap_local_size("cat\nlocal-sd-turbo") == "cat\nlocal-sd-turbo\n512x512"


def test_installed_wrapper_uses_context_local_endpoint(monkeypatch):
    calls = []

    def resolver(spec, owner=None):
        return ("https://cloud.example/v1/chat/completions", spec, {})

    async def generate(content, session_id=None, owner=None):
        calls.append(content)
        return {"ok": True}

    module = SimpleNamespace(_resolve_model=resolver, do_generate_image=generate)
    install_image_generation_defaults(module)

    selected = ConfiguredImageEndpoint(
        endpoint_id="local-image",
        model="local-sd-turbo",
        base_url="http://127.0.0.1:7861/v1",
        headers={},
    )
    monkeypatch.setattr(
        "src.image_generation_defaults.resolve_configured_image_endpoint",
        lambda owner: selected,
    )

    result = asyncio.run(module.do_generate_image("a cat\ngpt-image-1\n1024x1024\nhigh", owner="alice"))

    assert result == {"ok": True}
    assert calls == ["a cat\nlocal-sd-turbo\n512x512\nhigh"]
    url, model, _headers = module._resolve_model("local-sd-turbo", owner="alice")
    # Outside the wrapped request no context override remains.
    assert url.startswith("https://cloud.example")
    assert model == "local-sd-turbo"
