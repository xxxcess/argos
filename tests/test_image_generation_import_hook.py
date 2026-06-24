"""Regression tests for lightweight image integration import hooks."""

from __future__ import annotations

import importlib


def test_ai_interaction_installs_image_default_wrapper():
    module = importlib.import_module("src.ai_interaction")
    assert module._local_image_defaults_installed is True


def test_tool_execution_installs_agent_image_dispatch_wrapper():
    module = importlib.import_module("src.tool_execution")
    assert module._image_default_dispatch_installed is True
