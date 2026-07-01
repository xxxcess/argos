"""Quest-local Voyage Memory vector namespace helpers."""

from __future__ import annotations

import re
from pathlib import Path

from src.constants import CHROMA_DIR
from src.runtime_profile import VENTURE_RUNTIME_ID, is_venture_runtime

_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def validate_quest_memory_session_id(session_id: str) -> str:
    session_id = str(session_id or "").strip()
    if not _SAFE_SESSION_ID.match(session_id):
        raise ValueError("Invalid Quest memory session_id")
    return session_id


def quest_memory_collection_name(session_id: str) -> str:
    sid = validate_quest_memory_session_id(session_id)
    return f"quest-memory:{sid}"


def quest_memory_path(session_id: str) -> Path:
    sid = validate_quest_memory_session_id(session_id)
    return Path(CHROMA_DIR) / "quest-memory" / sid


def quest_memory_metadata(session_id: str) -> dict:
    sid = validate_quest_memory_session_id(session_id)
    return {
        "runtime_id": VENTURE_RUNTIME_ID,
        "session_id": sid,
        "collection": quest_memory_collection_name(sid),
        "path": str(quest_memory_path(sid)),
    }


def assert_quest_memory_runtime() -> None:
    if not is_venture_runtime():
        raise RuntimeError("Quest-local Voyage Memory is available only in the Argos Venture runtime")


def assert_quest_memory_metadata(session_id: str, metadata: dict) -> None:
    sid = validate_quest_memory_session_id(session_id)
    if not isinstance(metadata, dict):
        raise ValueError("Quest memory metadata is required")
    if metadata.get("runtime_id") != VENTURE_RUNTIME_ID or metadata.get("session_id") != sid:
        raise ValueError("Quest memory metadata does not match the requested Quest")

