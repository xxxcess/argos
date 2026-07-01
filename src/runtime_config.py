"""Runtime profile loading independent of application imports."""

from __future__ import annotations

import os
from pathlib import Path

from src.runtime_paths import get_app_root


def _parse_env_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_runtime_profile_env() -> None:
    """Load the selected runtime profile without inspecting the Git branch.

    Explicit process environment wins. When no runtime environment is provided,
    direct Python/uvicorn startup uses the branch-owned profile file just like
    the macOS launcher does.
    """
    profile_path = Path(
        os.environ.get("ARGOS_RUNTIME_PROFILE_FILE")
        or Path(get_app_root()) / "config" / "runtime-profile.env"
    )
    if profile_path.exists():
        for line in profile_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, raw_value = stripped.split("=", 1)
            key = key.strip()
            if not key.startswith("ARGOS_"):
                continue
            os.environ.setdefault(key, _parse_env_value(raw_value))

    if not os.environ.get("ARGOS_DATA_DIR"):
        storage_slug = os.environ.get("ARGOS_STORAGE_SLUG")
        if storage_slug:
            root = os.environ.get("ARGOS_DATA_ROOT") or str(
                Path.home() / "Library" / "Application Support" / "Argos" / "runtimes"
            )
            os.environ["ARGOS_DATA_DIR"] = str(Path(root) / storage_slug)

    if os.environ.get("ARGOS_DATA_DIR") and not os.environ.get("ODYSSEUS_DATA_DIR"):
        os.environ["ODYSSEUS_DATA_DIR"] = os.environ["ARGOS_DATA_DIR"]

