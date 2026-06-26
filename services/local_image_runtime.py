"""Probe and repair the optional local Diffusers image runtime.

The local image service and the video-anchor path share Argos's Python
interpreter. A broad package upgrade can therefore leave Diffusers with a
Transformers v5-style environment that cannot expose ``PreTrainedModel`` to the
current Diffusers loader. This module provides a no-terminal, UI-pollable repair
that restores a coherent v4-compatible dependency set without replacing the
host's Apple-Silicon PyTorch wheel.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from src.constants import DATA_DIR

_LOG_PATH = Path(DATA_DIR) / "logs" / "local_image_runtime_repair.log"
_PACKAGES = [
    "transformers>=4.57,<5",
    "diffusers>=0.30,<1",
    "accelerate>=1,<2",
    "safetensors>=0.4",
    "peft>=0.10,<1",
]
_PROBE = r'''
import json
from importlib.metadata import PackageNotFoundError, version

out = {"available": False, "versions": {}}
for name in ("torch", "transformers", "diffusers", "accelerate", "safetensors", "peft"):
    try:
        out["versions"][name] = version(name)
    except PackageNotFoundError:
        out["versions"][name] = None
try:
    import torch
    import transformers
    from transformers import PreTrainedModel
    import diffusers
    from diffusers import AutoPipelineForText2Image
    out.update({"available": True, "reason": None})
except Exception as exc:
    out.update({"available": False, "reason": f"{type(exc).__name__}: {str(exc)[:500]}"})
print(json.dumps(out))
'''
_LOCK = threading.RLock()
_REPAIRING = False
_LAST_ERROR = ""
_LAST_FINISHED = 0.0
_CACHE: tuple[float, dict[str, Any]] | None = None


def _probe() -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _PROBE],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=45,
            check=False,
        )
        text = (completed.stdout or "").strip()
        # The probe prints one JSON object. Preserve unexpected output for
        # diagnostics rather than treating it as a successful compatibility check.
        payload = json.loads(text.splitlines()[-1]) if text else {}
        if not isinstance(payload, dict):
            raise ValueError("runtime probe returned no structured result")
        payload["available"] = bool(payload.get("available"))
        if completed.returncode and not payload.get("reason"):
            payload["reason"] = text[-500:] or "The local image runtime probe failed."
        return payload
    except Exception as exc:
        return {"available": False, "reason": f"Could not inspect local image runtime: {type(exc).__name__}: {str(exc)[:400]}", "versions": {}}


def status(*, refresh: bool = False) -> dict[str, Any]:
    global _CACHE
    now = time.monotonic()
    if refresh or _CACHE is None or now - _CACHE[0] > 5:
        _CACHE = (now, _probe())
    with _LOCK:
        repairing = _REPAIRING
        error = _LAST_ERROR
        finished = _LAST_FINISHED
    value = dict(_CACHE[1])
    value.update({
        "repairing": repairing,
        "last_error": error or None,
        "last_repair_finished_at": finished or None,
        "repair_available": not repairing,
        # A serving process imports modules once. Even when the subprocess probe
        # is healthy after repair, the Cookbook-managed local server needs a
        # regular UI restart to load the repaired packages.
        "restart_required": bool(finished and value.get("available")),
    })
    return value


def _repair_worker() -> None:
    global _REPAIRING, _LAST_ERROR, _LAST_FINISHED, _CACHE
    error = ""
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _LOG_PATH.open("wb") as log:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", "--no-cache-dir", *_PACKAGES],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=30 * 60,
            )
        if result.returncode != 0:
            error = "The local image runtime repair did not complete. Open repair details for the installer output."
        else:
            checked = _probe()
            if not checked.get("available"):
                error = checked.get("reason") or "The repaired local image runtime still failed validation."
    except subprocess.TimeoutExpired:
        error = "The local image runtime repair timed out."
    except Exception as exc:
        error = f"Local image runtime repair failed: {type(exc).__name__}: {str(exc)[:300]}"
    finally:
        with _LOCK:
            _REPAIRING = False
            _LAST_ERROR = error
            _LAST_FINISHED = time.time()
            _CACHE = None


def start_repair() -> dict[str, Any]:
    global _REPAIRING, _LAST_ERROR
    current = status()
    if current.get("available"):
        return {"accepted": True, "already_ready": True, "status": current}
    with _LOCK:
        if _REPAIRING:
            return {"accepted": True, "already_repairing": True, "status": status()}
        _REPAIRING = True
        _LAST_ERROR = ""
        threading.Thread(target=_repair_worker, name="local-image-runtime-repair", daemon=True).start()
    return {"accepted": True, "status": status()}


def repair_log_tail(limit: int = 6000) -> str:
    try:
        text = _LOG_PATH.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    return text[-max(1, min(int(limit), 12000)):]
