"""Managed setup for the optional native mlx-video runtime.

This keeps the video feature on the same product path as Image Generation:
settings can validate readiness, install the optional local engine, and present
clear recovery states without requiring a user to run shell commands or edit
environment variables.
"""
from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from src.constants import DATA_DIR

_RUNTIME_ROOT = Path(DATA_DIR) / "video_runtime"
_LOG_PATH = Path(DATA_DIR) / "logs" / "video_runtime_install.log"
_WRAPPER = _RUNTIME_ROOT / "argos_mlx_video.py"
_PACKAGE = "git+https://github.com/Blaizzy/mlx-video.git"
_LOCK = threading.RLock()
_INSTALLING = False
_LAST_INSTALL_ERROR = ""
_LAST_INSTALL_FINISHED = 0.0


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


def _write_wrapper() -> Path:
    """Create a stable app-managed entrypoint for the installed package.

    The service owns a small compatibility wrapper so it can use the exact
    interpreter Argos runs under and normalize the LTX CLI's current
    ``--output-path`` spelling. Users never see or invoke this file.
    """
    _RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    wrapper = '''#!{python}\n"""Argos-managed mlx-video compatibility entrypoint."""\nimport sys\n\ndef main():\n    argv = ["--output-path" if item == "--output" else item for item in sys.argv[1:]]\n    sys.argv = [sys.argv[0], *argv]\n    from mlx_video.models.ltx_2.generate import main as upstream_main\n    return upstream_main()\n\nif __name__ == "__main__":\n    main()\n'''.format(python=sys.executable)
    _WRAPPER.write_text(wrapper, encoding="utf-8")
    _WRAPPER.chmod(_WRAPPER.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return _WRAPPER


def prepare_runtime() -> dict[str, Any]:
    """Make the installed runtime discoverable for the existing video worker."""
    reason = _host_reason()
    if reason:
        return {"available": False, "reason": reason}
    if not _package_available():
        return {"available": False, "reason": "Video engine is not installed yet."}
    try:
        wrapper = _write_wrapper()
        os.environ["MLX_VIDEO_BIN"] = str(wrapper)
    except Exception as exc:
        return {"available": False, "reason": f"Video engine is installed but could not be prepared: {exc}"[:400]}
    return {"available": True, "binary": str(wrapper)}


def status() -> dict[str, Any]:
    prepared = prepare_runtime()
    with _LOCK:
        installing = _INSTALLING
        error = _LAST_INSTALL_ERROR
        finished = _LAST_INSTALL_FINISHED
    return {
        "supported": _host_reason() is None,
        "available": bool(prepared.get("available")),
        "installing": installing,
        "reason": prepared.get("reason"),
        "last_error": error or None,
        "last_install_finished_at": finished or None,
        "managed": True,
        "package": "mlx-video",
        "action": "install" if not prepared.get("available") and not installing else None,
    }


def _install_worker() -> None:
    global _INSTALLING, _LAST_INSTALL_ERROR, _LAST_INSTALL_FINISHED
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    error = ""
    try:
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
            error = "The video engine installation did not complete. Open the setup details for the recorded installer output."
        elif not prepare_runtime().get("available"):
            error = "The video engine installed but could not be initialized."
    except subprocess.TimeoutExpired:
        error = "The video engine installation timed out."
    except Exception:
        error = "The video engine installation could not start."
    finally:
        with _LOCK:
            _INSTALLING = False
            _LAST_INSTALL_ERROR = error
            _LAST_INSTALL_FINISHED = time.time()


def start_install() -> dict[str, Any]:
    """Start an admin-requested local install, returning immediately for UI polling."""
    global _INSTALLING, _LAST_INSTALL_ERROR
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
        threading.Thread(target=_install_worker, name="mlx-video-install", daemon=True).start()
    return {"accepted": True, "status": status()}


def install_log_tail(limit: int = 4000) -> str:
    try:
        text = _LOG_PATH.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    return text[-max(1, min(int(limit), 12000)):]
