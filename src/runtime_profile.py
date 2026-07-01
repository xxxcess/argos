"""Runtime profile helpers.

Product behavior is selected by explicit runtime identity, never by the
checked-out Git branch.
"""

from __future__ import annotations

import os
from pathlib import Path

from src.constants import CHROMA_DIR, DATA_DIR

NIGHTLY_RUNTIME_ID = "nightly"
VENTURE_RUNTIME_ID = "argos-venture"


def runtime_id() -> str:
    return (os.getenv("ARGOS_RUNTIME_ID") or NIGHTLY_RUNTIME_ID).strip() or NIGHTLY_RUNTIME_ID


def is_venture_runtime() -> bool:
    return runtime_id() == VENTURE_RUNTIME_ID


def require_venture_runtime() -> None:
    if not is_venture_runtime():
        from fastapi import HTTPException

        raise HTTPException(404, "Not found")


def runtime_summary() -> dict:
    rid = runtime_id()
    return {
        "runtime_id": rid,
        "product": "Argos Venture" if rid == VENTURE_RUNTIME_ID else "Odysseus",
        "is_venture": rid == VENTURE_RUNTIME_ID,
        "data_dir": DATA_DIR,
        "database_path": str(Path(DATA_DIR) / "app.db"),
        "chroma_path": CHROMA_DIR,
        "quest_memory_root": str(Path(CHROMA_DIR) / "quest-memory"),
    }

