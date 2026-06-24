"""Argos source package bootstrap hooks.

Most of the project historically used ``src`` as a namespace package.  The
small import hook below preserves that layout while installing the local image
endpoint preference wrapper exactly when ``src.ai_interaction`` is imported.
It avoids importing optional Diffusers/PyTorch at normal application startup.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import sys


_TARGET = "src.ai_interaction"


class _AiInteractionLoader(importlib.abc.Loader):
    def __init__(self, wrapped_loader):
        self._wrapped_loader = wrapped_loader

    def create_module(self, spec):  # pragma: no cover - delegated import protocol
        create = getattr(self._wrapped_loader, "create_module", None)
        return create(spec) if callable(create) else None

    def exec_module(self, module):
        self._wrapped_loader.exec_module(module)
        from src.image_generation_defaults import install_image_generation_defaults

        install_image_generation_defaults(module)


class _AiInteractionFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):  # pragma: no cover - import system glue
        if fullname != _TARGET:
            return None
        # Call PathFinder directly rather than importlib.util.find_spec() so this
        # finder does not recurse into itself.
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec and spec.loader and not isinstance(spec.loader, _AiInteractionLoader):
            spec.loader = _AiInteractionLoader(spec.loader)
        return spec


if not any(isinstance(finder, _AiInteractionFinder) for finder in sys.meta_path):
    sys.meta_path.insert(0, _AiInteractionFinder())
