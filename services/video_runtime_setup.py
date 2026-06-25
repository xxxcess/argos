"""Managed local runtime and model setup for anchor-first video generation.

``mlx-video`` is the Apple-Silicon inference runtime, not the video model. This
module makes the runtime, the selected LTX-2 distilled weights, their cache, and
the Argos compatibility entrypoint one guided product operation. The user never
needs to run pip, set an executable path, or manually fetch model files.
"""
from __future__ import annotations

import importlib.util
import json
import os
import platform
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from src.constants import DATA_DIR

_RUNTIME_ROOT = Path(DATA_DIR) / "video_runtime"
_MODEL_CACHE = Path(DATA_DIR) / "models" / "mlx-video"
_MODEL_STATE = _RUNTIME_ROOT / "model.json"
_LOG_PATH = Path(DATA_DIR) / "logs" / "video_runtime_install.log"
_WRAPPER = _RUNTIME_ROOT / "argos_mlx_video.py"
_PACKAGE = "git+https://github.com/Blaizzy/mlx-video.git"
# The mlx-video project publishes this pre-converted MLX checkpoint for the
# fast distilled LTX path. Pinning the selected model makes cache/readiness
# visible and avoids treating an arbitrary runtime package as a model.
_MODEL_REPO = "prince-canuma/LTX-2-distilled"
_LOCK = threading.RLock()
_INSTALLING = False
_LAST_INSTALL_ERROR = ""
_LAST_INSTALL_FINISHED = 0.0
_INSTALL_PHASE = "not_installed"


def _host_reason() -> str | None:
    if platform.system() != "Darwin":
        return "Video generation runs locally on native macOS with Apple Silicon."
    if platform.machine().lower() not in {"arm64", "aarch64"}:
        return "Video generation requires an Apple Silicon Mac."
    if sys.version_info < (3, 11):
        return "Video generation requires Python 3.11 or newer."
    return None


def _package_available() -> bool:
    return importlib.util.find_spec("mlx_video") is not None


def _model_path() -> Path | None:
    """Read a successfully completed model download, never guessing from cache."""
    try:
        saved = json.loads(_MODEL_STATE.read_text(encoding="utf-8"))
        path = Path(str(saved.get("path") or ""))
        repo = str(saved.get("repo") or "")
        if repo == _MODEL_REPO and path.is_dir() and any(path.glob("*.json")):
            return path
    except Exception:
        pass
    return None


def _write_model_state(path: Path) -> None:
    _RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    temp = _MODEL_STATE.with_suffix(".tmp")
    temp.write_text(json.dumps({"repo": _MODEL_REPO, "path": str(path), "installed_at": time.time()}, indent=2), encoding="utf-8")
    os.replace(temp, _MODEL_STATE)


def _write_wrapper(model_path: Path) -> Path:
    """Create a stable app-managed CLI entrypoint.

    The worker invokes this rather than an environment-dependent console script.
    It also injects the downloaded local model path and normalizes the upstream
    output flag while remaining compatible with either spelling.
    """
    _RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    wrapper = '''#!{python}
"""Argos-managed mlx-video compatibility entrypoint."""
import os
import sys

def main():
    argv = ["--output-path" if item == "--output" else item for item in sys.argv[1:]]
    model_path = os.environ.get("ARGOS_VIDEO_MODEL_PATH", "").strip()
    if model_path and "--model-repo" not in argv:
        argv.extend(["--model-repo", model_path])
    sys.argv = [sys.argv[0], *argv]
    from mlx_video.models.ltx_2.generate import main as upstream_main
    return upstream_main()

if __name__ == "__main__":
    main()
'''.format(python=sys.executable)
    _WRAPPER.write_text(wrapper, encoding="utf-8")
    _WRAPPER.chmod(_WRAPPER.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return _WRAPPER


def prepare_runtime() -> dict[str, Any]:
    """Prepare the runtime only when both runtime and LTX weights are ready."""
    reason = _host_reason()
    if reason:
        return {"available": False, "reason": reason, "model_repo": _MODEL_REPO}
    if not _package_available():
        return {"available": False, "reason": "Video engine is not installed yet.", "model_repo": _MODEL_REPO}
    model_path = _model_path()
    if model_path is None:
        return {
            "available": False,
            "reason": "The LTX-2 distilled model has not been downloaded yet.",
            "model_repo": _MODEL_REPO,
        }
    try:
        wrapper = _write_wrapper(model_path)
        os.environ["MLX_VIDEO_BIN"] = str(wrapper)
        os.environ["ARGOS_VIDEO_MODEL_PATH"] = str(model_path)
    except Exception as exc:
        return {
            "available": False,
            "reason": f"Video engine is installed but could not be prepared: {exc}"[:400],
            "model_repo": _MODEL_REPO,
        }
    return {
        "available": True,
        "binary": str(wrapper),
        "model_repo": _MODEL_REPO,
        "model_path": str(model_path),
    }


def status() -> dict[str, Any]:
    prepared = prepare_runtime()
    with _LOCK:
        installing = _INSTALLING
        error = _LAST_INSTALL_ERROR
        finished = _LAST_INSTALL_FINISHED
        phase = _INSTALL_PHASE
    return {
        "supported": _host_reason() is None,
        "available": bool(prepared.get("available")),
        "installing": installing,
        "phase": phase,
        "reason": prepared.get("reason"),
        "last_error": error or None,
        "last_install_finished_at": finished or None,
        "managed": True,
        "runtime_package": "mlx-video",
        "model_repo": _MODEL_REPO,
        "model_ready": _model_path() is not None,
        "action": "install" if not prepared.get("available") and not installing else None,
    }


def _install_runtime_and_model() -> None:
    global _INSTALLING, _LAST_INSTALL_ERROR, _LAST_INSTALL_FINISHED, _INSTALL_PHASE
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    error = ""
    try:
        with _LOCK:
            _INSTALL_PHASE = "installing_runtime"
        with _LOG_PATH.open("wb") as log:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", _PACKAGE],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=30 * 60,
            )
        if result.returncode != 0:
            error = "The mlx-video runtime installation did not complete. Open setup details for the recorded installer output."
            return
        with _LOCK:
            _INSTALL_PHASE = "downloading_ltx_model"
        # Use an application-owned cache and record the resolved snapshot path.
        # mlx-video accepts a local path through --model-repo, so later jobs never
        # need to rediscover or silently re-download the selected weights.
        from huggingface_hub import snapshot_download
        _MODEL_CACHE.mkdir(parents=True, exist_ok=True)
        path = Path(snapshot_download(
            repo_id=_MODEL_REPO,
            cache_dir=str(_MODEL_CACHE),
            resume_download=True,
            allow_patterns=["*.safetensors", "*.json"],
        ))
        _write_model_state(path)
        if not prepare_runtime().get("available"):
            error = "The LTX-2 model download completed but could not be initialized."
    except subprocess.TimeoutExpired:
        error = "The video runtime installation timed out."
    except Exception as exc:
        error = f"The LTX-2 model setup could not complete: {type(exc).__name__}: {str(exc)[:220]}"
    finally:
        with _LOCK:
            _INSTALLING = False
            _LAST_INSTALL_ERROR = error
            _LAST_INSTALL_FINISHED = time.time()
            _INSTALL_PHASE = "ready" if not error and prepare_runtime().get("available") else "failed"


def start_install() -> dict[str, Any]:
    """Install runtime and model in one admin-requested, UI-pollable operation."""
    global _INSTALLING, _LAST_INSTALL_ERROR, _INSTALL_PHASE
    reason = _host_reason()
    if reason:
        return {"accepted": False, "status": status(), "error": reason}
    if prepare_runtime().get("available"):
        return {"accepted": True, "already_ready": True, "status": status()}
    with _LOCK:
        if _INSTALLING:
            return {"accepted": True, "already_installing": True, "status": status()}
        _INSTALLING = True
        _LAST_INSTALL_ERROR = ""
        _INSTALL_PHASE = "queued"
        threading.Thread(target=_install_runtime_and_model, name="mlx-video-setup", daemon=True).start()
    return {"accepted": True, "status": status()}


def install_log_tail(limit: int = 4000) -> str:
    try:
        text = _LOG_PATH.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    return text[-max(1, min(int(limit), 12000)):]
