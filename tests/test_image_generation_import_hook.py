"""Regression test for the lightweight ai_interaction import integration."""

from __future__ import annotations

import importlib


def test_ai_interaction_installs_image_default_wrapper():
    module = importlib.import_module("src.ai_interaction")
    assert module._local_image_defaults_installed is True
