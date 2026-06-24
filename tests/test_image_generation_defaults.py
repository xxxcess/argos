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


def test_installed_wrapper_routes_selected_endpoint_without_model_lookup(monkeypatch):
    fallback_calls = []
    direct_calls = []

    async def legacy_generate(content, session_id=None, owner=None):
        fallback_calls.append((content, session_id, owner))
        return {"legacy": True}

    module = SimpleNamespace(do_generate_image=legacy_generate)
    install_image_generation_defaults(module)

    selected = ConfiguredImageEndpoint(
        endpoint_id="local-image",
        endpoint_name="Local Diffusers Image",
        model="local-sd-turbo",
        base_url="http://127.0.0.1:7861/v1",
        headers={},
        is_local=True,
    )

    async def direct_generate(content, endpoint, *, session_id=None, owner=None):
        direct_calls.append((content, endpoint, session_id, owner))
        return {"image_url": "/api/generated-image/cat.png"}

    monkeypatch.setattr(
        "src.image_generation_defaults.resolve_configured_image_endpoint",
        lambda owner: selected,
    )
    monkeypatch.setattr("src.image_generation_defaults.generate_configured_image", direct_generate)

    result = asyncio.run(module.do_generate_image("a cat\ngpt-image-1\n1024x1024\nhigh", session_id="s1", owner="alice"))

    assert result == {"image_url": "/api/generated-image/cat.png"}
    assert fallback_calls == []
    assert direct_calls == [("a cat\ngpt-image-1\n1024x1024\nhigh", selected, "s1", "alice")]


def test_unconfigured_wrapper_keeps_legacy_cloud_path(monkeypatch):
    fallback_calls = []

    async def legacy_generate(content, session_id=None, owner=None):
        fallback_calls.append((content, session_id, owner))
        return {"legacy": True}

    module = SimpleNamespace(do_generate_image=legacy_generate)
    install_image_generation_defaults(module)
    monkeypatch.setattr("src.image_generation_defaults.resolve_configured_image_endpoint", lambda owner: None)

    result = asyncio.run(module.do_generate_image("a cloud cat", owner="alice"))

    assert result == {"legacy": True}
    assert fallback_calls == [("a cloud cat", None, "alice")]