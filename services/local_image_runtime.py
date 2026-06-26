"""Probe and repair the optional local Diffusers image runtime.

The local image service and the video-anchor path share Argos's Python
interpreter. A broad package upgrade can leave Diffusers with an incompatible
Transformers install, or leave a broken optional torchvision package that masks
the real import error. This module performs a no-terminal, UI-pollable repair
without replacing the host's Apple-Silicon PyTorch wheel.
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
# Exact, v4-compatible packages. These are reinstalled without dependencies so
# pip cannot silently replace the macOS torch wheel while repairing Diffusers.
_PACKAGES = [
    "transformers==4.57.1",
    "tokenizers==0.22.2",
    "diffusers==0.30.3",
    "accelerate==0.34.2",
    "safetensors==0.4.5",
    "peft==0.13.2",
    "huggingface-hub<1",
]
_PROBE = r'''
import json
import traceback
from importlib.metadata import PackageNotFoundError, version

out = {"available": False, "versions": {}, "stage": "starting"}
for name in ("torch", "torchvision", "transformers", "tokenizers", "diffusers", "accelerate", "safetensors", "peft", "huggingface-hub"):
    try:
        out["versions"][name] = version(name)
    except PackageNotFoundError:
        out["versions"][name] = None
try:
    out["stage"] = "import torch"
    import torch
    out["stage"] = "import transformers"
    import transformers
    out["stage"] = "import transformers.PreTrainedModel"
    from transformers import PreTrainedModel
    out["stage"] = "import diffusers"
    import diffusers
    out["stage"] = "import diffusers.AutoPipelineForText2Image"
    from diffusers import AutoPipelineForText2Image
    out.update({"available": True, "reason": None})
except Exception as exc:
    out.update({
        "available": False,
        "reason": f"{out['stage']}: {type(exc).__name__}: {str(exc)[:700]}",
        "traceback": traceback.format_exc(limit=18)[-5000:],
    })
try:
    import torchvision
    out["torchvision_available"] = True
except Exception:
    out["torchvision_available"] = False
    out["torchvision_error"] = traceback.format_exc(limit=12)[-3000:]
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
            timeout=60,
            check=False,
        )
        text = (completed.stdout or "").strip()
        payload = json.loads(text.splitlines()[-1]) if text else {}
        if not isinstance(payload, dict):
            raise ValueError("runtime probe returned no structured result")
        payload["available"] = bool(payload.get("available"))
        if completed.returncode and not payload.get("reason"):
            payload["reason"] = text[-900:] or "The local image runtime probe failed."
        return payload
    except Exception as exc:
        return {
            "available": False,
            "reason": f"Could not inspect local image runtime: {type(exc).__name__}: {str(exc)[:500]}",
            "versions": {},
        }


def _write_probe(log, probe: dict[str, Any], heading: str) -> None:
    log.write(f"\n\n===== {heading} =====\n")
    log.write(json.dumps(probe, indent=2, sort_keys=True))
    log.write("\n")


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
        # A serving process imports modules once. Even when the child-process
        # probe is healthy after repair, Cookbook must restart Local Diffusers.
        "restart_required": bool(finished and value.get("available")),
    })
    return value


def _repair_worker() -> None:
    global _REPAIRING, _LAST_ERROR, _LAST_FINISHED, _CACHE
    error = ""
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _LOG_PATH.open("w", encoding="utf-8", errors="replace") as log:
            log.write("Argos local image runtime repair\n")
            log.write("Reinstalling pinned Diffusers/Transformers packages without changing torch.\n")
            result = subprocess.run(
                [
                    sys.executable, "-m", "pip", "install", "--force-reinstall",
                    "--no-deps", "--no-cache-dir", *_PACKAGES,
                ],
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
                _write_probe(log, checked, "import probe after pinned reinstall")
                # A mismatched optional torchvision frequently breaks the
                # Transformers lazy import path. It is not required for this
                # SD-Turbo text-to-image profile; remove it only when its own
                # import fails, never replace torch or install a generic wheel.
                if not checked.get("available") and checked.get("torchvision_error"):
                    log.write("\nOptional torchvision import is broken; removing it so Transformers uses its PIL fallback.\n")
                    removed = subprocess.run(
                        [sys.executable, "-m", "pip", "uninstall", "-y", "torchvision"],
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        check=False,
                        timeout=10 * 60,
                    )
                    if removed.returncode != 0:
                        error = "The image runtime repair could not remove a broken optional torchvision install. Open repair details."
                    else:
                        checked = _probe()
                        _write_probe(log, checked, "import probe after optional torchvision removal")
                if not error and not checked.get("available"):
                    error = checked.get("reason") or "The repaired local image runtime still failed validation."
    except subprocess.TimeoutExpired:
        error = "The local image runtime repair timed out."
    except Exception as exc:
        error = f"Local image runtime repair failed: {type(exc).__name__}: {str(exc)[:400]}"
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
