"""Argos source package bootstrap hooks.

The project historically used ``src`` as a namespace package. These narrowly
scoped import hooks install optional image and local-video integrations only
when their established dispatch modules load, keeping ML/depth imports out of
normal application startup.
"""
from __future__ import annotations

import importlib.abc
import importlib.machinery
import sys

_TARGETS = {
    "src.ai_interaction": "image_defaults",
    "src.tool_execution": "agent_media_dispatch",
    "src.agent_loop": "agent_image_terminal",
    "src.agent_tools": "video_tags",
    "src.tool_schemas": "video_schema",
}


class _IntegrationLoader(importlib.abc.Loader):
    def __init__(self, wrapped_loader, hook_name: str):
        self._wrapped_loader = wrapped_loader
        self._hook_name = hook_name

    def create_module(self, spec):  # pragma: no cover - delegated import protocol
        create = getattr(self._wrapped_loader, "create_module", None)
        return create(spec) if callable(create) else None

    def exec_module(self, module):
        self._wrapped_loader.exec_module(module)
        if self._hook_name == "image_defaults":
            from src.image_generation_defaults import install_image_generation_defaults
            from src.image_tool_payload import normalize_image_tool_content
            install_image_generation_defaults(module)
            original_generate = module.do_generate_image

            async def generate_with_normalized_tool_content(content, session_id=None, owner=None):
                return await original_generate(normalize_image_tool_content(content), session_id=session_id, owner=owner)

            module.do_generate_image = generate_with_normalized_tool_content
            module._local_image_tool_content_normalized = True
        elif self._hook_name == "agent_media_dispatch":
            from src.generate_image_dispatch import install_generate_image_dispatch
            from src.video_tool_dispatch import install_video_generation_dispatch
            install_generate_image_dispatch(module)
            install_video_generation_dispatch(module)
        elif self._hook_name == "agent_image_terminal":
            from src.image_agent_completion import install_image_agent_terminal
            install_image_agent_terminal(module)
        elif self._hook_name == "video_tags":
            from src.video_tool_dispatch import install_video_agent_tags
            install_video_agent_tags(module)
        elif self._hook_name == "video_schema":
            from src.video_tool_dispatch import install_video_tool_schema
            install_video_tool_schema(module)


class _IntegrationFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):  # pragma: no cover - import-system glue
        hook_name = _TARGETS.get(fullname)
        if not hook_name:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec and spec.loader and not isinstance(spec.loader, _IntegrationLoader):
            spec.loader = _IntegrationLoader(spec.loader, hook_name)
        return spec


if not any(isinstance(finder, _IntegrationFinder) for finder in sys.meta_path):
    sys.meta_path.insert(0, _IntegrationFinder())
