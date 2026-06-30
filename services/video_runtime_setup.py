"""Managed local setup for the low-memory depth/parallax video engine.

The local image server and this renderer run inside the same Argos process.
Video setup must therefore *not* broadly upgrade shared PyTorch, Diffusers, or
Transformers packages: doing so can break an already-running Cookbook image
pipeline. The app owns torch/transformers as core dependencies; this setup only
provisions the FFmpeg helper and the small depth checkpoint.
"""
from __future__ import annotations

import importlib.util
import json
import os
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from src.constants import DATA_DIR

_RUNTIME_ROOT = Path(DATA_DIR) / "video_runtime"
_MODEL_CACHE = Path(DATA_DIR) / "models" / "depth-anything-v2"
_STATE_PATH = _RUNTIME_ROOT / "depth_parallax.json"
_LOG_PATH = Path(DATA_DIR) / "logs" / "depth_parallax_install.log"
_MODEL_REPO = "depth-anything/Depth-Anything-V2-Small-hf"
# Do not add torch, transformers, diffusers, or peft here. They are shared by
# Argos and the Cookbook Image Default; compatibility repair is handled by
# services.local_image_runtime as a separate explicit operation.
_VIDEO_PACKAGES = ["imageio-ffmpeg>=0.5"]
_LOCK = threading.RLock()
_INSTALLING = False
_LAST_ERROR = ""
_LAST_FINISHED = 0.0
_PHASE = "not_installed"


def _host_reason() -> str | None:
    if platform.system() != "Darwin":
        return "Local depth-parallax video is currently supported on native macOS."
    if platform.machine().lower() not in {"arm64", "aarch64"}:
        return "Local depth-parallax video requires Apple Silicon."
    if sys.version_info < (3, 10):
        return "Local depth-parallax video requires Python 3.10 or newer."
    return None


def _packages_ready() -> bool:
    return all(importlib.util.find_spec(name) is not None for name in ("torch", "transformers", "numpy", "PIL", "imageio_ffmpeg"))


def _read_state() -> dict[str, Any]:
    try:
        value = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _write_state(value: dict[str, Any]) -> None:
    _RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    temporary = _STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(temporary, _STATE_PATH)


def _model_path() -> Path | None:
    state = _read_state()
    path = Path(str(state.get("model_path") or ""))
    if state.get("model_repo") == _MODEL_REPO and path.is_dir() and (path / "config.json").is_file():
        return path
    return None


def _ffmpeg_path() -> str:
    try:
        import imageio_ffmpeg
        path = str(imageio_ffmpeg.get_ffmpeg_exe() or "")
        return path if Path(path).is_file() else ""
    except Exception:
        return ""


def _supports_minterpolate(binary: str) -> bool:
    if not binary:
        return False
    try:
        result = subprocess.run([binary, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=20, check=False)
        return result.returncode == 0 and "minterpolate" in (result.stdout or "")
    except Exception:
        return False


def prepare_runtime() -> dict[str, Any]:
    reason = _host_reason()
    if reason:
        return {"available": False, "reason": reason, "model_repo": _MODEL_REPO}
    if not _packages_ready():
        return {
            "available": False,
            "reason": "Argos is missing a core depth dependency (torch, transformers, numpy, or Pillow). Update the app runtime before installing local video.",
            "model_repo": _MODEL_REPO,
        }
    model_path = _model_path()
    if model_path is None:
        return {"available": False, "reason": "The small depth model has not been downloaded yet.", "model_repo": _MODEL_REPO}
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        return {"available": False, "reason": "The managed FFmpeg binary is unavailable.", "model_repo": _MODEL_REPO}
    if not _supports_minterpolate(ffmpeg):
        return {"available": False, "reason": "The managed FFmpeg binary does not include motion interpolation.", "model_repo": _MODEL_REPO}
    os.environ["ARGOS_DEPTH_VIDEO_MODEL_PATH"] = str(model_path)
    os.environ["ARGOS_DEPTH_VIDEO_FFMPEG"] = ffmpeg
    return {
        "available": True,
        "provider": "depth_parallax",
        "model_repo": _MODEL_REPO,
        "model_path": str(model_path),
        "ffmpeg": ffmpeg,
        "profile": "512x512 · 8 seconds · 24 FPS · muted",
    }


def status() -> dict[str, Any]:
    prepared = prepare_runtime()
    with _LOCK:
        installing = _INSTALLING
        error = _LAST_ERROR
        finished = _LAST_FINISHED
        phase = _PHASE
    return {
        **prepared,
        "supported": _host_reason() is None,
        "installing": installing,
        "phase": phase,
        "last_error": error or None,
        "last_install_finished_at": finished or None,
        "managed": True,
        "runtime_packages": ["imageio-ffmpeg"],
        "model_ready": _model_path() is not None,
        "action": "install" if not prepared.get("available") and not installing else None,
    }


def _install_worker() -> None:
    global _INSTALLING, _LAST_ERROR, _LAST_FINISHED, _PHASE
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    error = ""
    try:
        if not all(importlib.util.find_spec(name) is not None for name in ("torch", "transformers", "numpy", "PIL")):
            error = "Argos is missing core image/depth dependencies. Update the app runtime before installing local video."
            return
        with _LOCK:
            _PHASE = "installing_video_helper"
        with _LOG_PATH.open("wb") as log:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", "--no-cache-dir", *_VIDEO_PACKAGES],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=30 * 60,
            )
        if result.returncode != 0:
            error = "The local FFmpeg helper installation did not complete. Open setup details for the installer output."
            return
        with _LOCK:
            _PHASE = "downloading_depth_model"
        from huggingface_hub import snapshot_download
        _MODEL_CACHE.mkdir(parents=True, exist_ok=True)
        path = Path(snapshot_download(
            repo_id=_MODEL_REPO,
            cache_dir=str(_MODEL_CACHE),
            resume_download=True,
            allow_patterns=["*.json", "*.safetensors", "*.bin", "*.txt"],
        ))
        _write_state({"model_repo": _MODEL_REPO, "model_path": str(path), "installed_at": time.time()})
        with _LOCK:
            _PHASE = "verifying_ffmpeg"
        if not prepare_runtime().get("available"):
            error = "The depth model downloaded but the local video engine could not be verified."
    except subprocess.TimeoutExpired:
        error = "The local depth-engine installation timed out."
    except Exception as exc:
        error = f"The local depth-engine setup could not complete: {type(exc).__name__}: {str(exc)[:220]}"
    finally:
        with _LOCK:
            _INSTALLING = False
            _LAST_ERROR = error
            _LAST_FINISHED = time.time()
            _PHASE = "ready" if not error and prepare_runtime().get("available") else "failed"


def start_install() -> dict[str, Any]:
    global _INSTALLING, _LAST_ERROR, _PHASE
    reason = _host_reason()
    if reason:
        return {"accepted": False, "error": reason, "status": status()}
    if prepare_runtime().get("available"):
        return {"accepted": True, "already_ready": True, "status": status()}
    with _LOCK:
        if _INSTALLING:
            return {"accepted": True, "already_installing": True, "status": status()}
        _INSTALLING = True
        _LAST_ERROR = ""
        _PHASE = "queued"
        threading.Thread(target=_install_worker, name="depth-parallax-setup", daemon=True).start()
    return {"accepted": True, "status": status()}


def install_log_tail(limit: int = 6000) -> str:
    try:
        text = _LOG_PATH.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    return text[-max(1, min(int(limit), 12000)):]
