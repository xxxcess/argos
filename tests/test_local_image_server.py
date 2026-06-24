"""Unit tests for the optional local Diffusers image server.

These tests never download a model or import Diffusers: a small fake pipeline
exercises the HTTP-compatible payload and memory-safety validation path.
"""

from __future__ import annotations

import asyncio
import base64

import pytest

from src.local_image_server import ImageGenerationRequest, LocalImageService, PROFILES, _parse_size


class _FakeImage:
    def save(self, out, format="PNG"):
        assert format == "PNG"
        out.write(b"\x89PNG\r\n\x1a\nlocal-image")


class _FakeResult:
    images = [_FakeImage()]


class _FakePipeline:
    def __init__(self):
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResult()


def _ready_service(max_size=512):
    service = LocalImageService(PROFILES["sd-turbo"], "sd-turbo", max_size=max_size)
    service.pipeline = _FakePipeline()
    service.device = "mps"
    service.status = "ready"
    return service


def test_parse_size_enforces_low_memory_bounds():
    assert _parse_size("512x512", 512) == (512, 512)
    with pytest.raises(ValueError, match="up to 512x512"):
        _parse_size("768x512", 512)
    with pytest.raises(ValueError, match="multiples of 8"):
        _parse_size("510x512", 512)


def test_local_generation_returns_openai_compatible_base64_png():
    service = _ready_service()
    result = asyncio.run(
        service.generate(
            ImageGenerationRequest(model="sd-turbo", prompt="a small orange cat", size="512x512", quality="medium")
        )
    )

    assert result["data"] and "b64_json" in result["data"][0]
    assert base64.b64decode(result["data"][0]["b64_json"]).startswith(b"\x89PNG")
    call = service.pipeline.calls[0]
    assert call["num_inference_steps"] == 1
    assert call["guidance_scale"] == 0.0


def test_local_generation_rejects_unsupported_batch_and_model():
    service = _ready_service()
    with pytest.raises(ValueError, match="n=1"):
        asyncio.run(service.generate(ImageGenerationRequest(prompt="cat", n=2)))
    with pytest.raises(ValueError, match="not available"):
        asyncio.run(service.generate(ImageGenerationRequest(prompt="cat", model="other")))


def test_health_reports_selected_profile_and_device():
    service = _ready_service()
    health = service.health()
    assert health["status"] == "ready"
    assert health["model"] == "sd-turbo"
    assert health["device"] == "mps"
    assert health["max_size"] == 512


def test_models_are_discoverable_before_pipeline_load():
    service = LocalImageService(PROFILES["sd-turbo"], "local-sd-turbo")
    assert service.pipeline is None
    assert service.health()["status"] == "starting"
    assert service.served_model_id == "local-sd-turbo"
