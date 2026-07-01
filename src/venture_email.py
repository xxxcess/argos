"""Argos Venture dynamic Email source polling.

The first release stores only Captain-owned email account references plus a
constrained scope. Polling consumes owner-scoped cached email metadata when
available and advances a QuestSourceCheckpoint; it never stores credentials or
falls back to scanning an entire mailbox.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

from core.database import QuestMemoryEntry, QuestSourceCheckpoint, QuestSourceVersion, utcnow_naive
from src.constants import EMAIL_CACHE_DB


def _loads(value, fallback):
    try:
        parsed = json.loads(value or "")
        return parsed if parsed is not None else fallback
    except Exception:
        return fallback


def _scope_matches(row: dict, scope: dict) -> bool:
    mailbox = scope.get("mailbox") or scope.get("folder")
    if mailbox and str(row.get("folder") or "") != str(mailbox):
        return False
    sender = str(scope.get("sender") or "").lower().strip()
    if sender and sender not in str(row.get("sender") or "").lower():
        return False
    subject = str(scope.get("subject") or "").lower().strip()
    if subject and subject not in str(row.get("subject") or "").lower():
        return False
    query = str(scope.get("query") or "").lower().strip()
    if query and query not in (str(row.get("subject") or "") + " " + str(row.get("tags") or "")).lower():
        return False
    label = str(scope.get("label") or "").lower().strip()
    if label and label not in str(row.get("tags") or "").lower():
        return False
    return True


def _cached_email_rows(owner: str, scope: dict, limit: int = 50) -> list[dict]:
    path = Path(EMAIL_CACHE_DB)
    if not path.exists():
        return []
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT uid, folder, subject, sender, tags, created_at
            FROM email_tags
            WHERE owner = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (owner or "", max(1, min(limit, 200))),
        ).fetchall()
        return [dict(row) for row in rows if _scope_matches(dict(row), scope)]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def poll_email_source(db, source) -> dict:
    config = _loads(source.configuration_json, {})
    scope = config.get("scope") if isinstance(config, dict) else None
    if not isinstance(scope, dict) or not any(scope.get(k) for k in ("mailbox", "folder", "label", "sender", "subject", "query")):
        raise ValueError("Email Quest Source requires a constrained scope")

    checkpoint = source.checkpoint
    if checkpoint is None:
        checkpoint = QuestSourceCheckpoint(id=uuid.uuid4().hex, quest_source_id=source.id)
        db.add(checkpoint)

    rows = _cached_email_rows(source.captain_username, scope)
    previous_cursor = checkpoint.cursor or ""
    fingerprint_payload = [
        {
            "uid": row.get("uid"),
            "folder": row.get("folder"),
            "subject": row.get("subject"),
            "sender": row.get("sender"),
            "created_at": row.get("created_at"),
        }
        for row in rows[:25]
    ]
    cursor = hashlib.sha256(json.dumps(fingerprint_payload, sort_keys=True).encode("utf-8")).hexdigest()
    now = utcnow_naive()
    checkpoint.last_polled_at = now
    checkpoint.last_success_at = now
    checkpoint.last_error = None
    checkpoint.cursor = cursor

    changed = bool(rows) and cursor != previous_cursor
    version = None
    if changed:
        version = QuestSourceVersion(
            id=uuid.uuid4().hex,
            quest_source_id=source.id,
            version_label=f"email-{now.strftime('%Y%m%d%H%M%S')}",
            source_fingerprint=cursor,
            provenance_json=json.dumps(
                {
                    "source_type": "email",
                    "account_id": config.get("account_id"),
                    "scope": scope,
                    "matched_records": len(rows),
                },
                sort_keys=True,
            ),
            captured_at=now,
        )
        db.add(version)
        for memory in db.query(QuestMemoryEntry).filter(
            QuestMemoryEntry.session_id == source.session_id,
            QuestMemoryEntry.category.in_(("finding", "risk", "contradiction", "source_update")),
            QuestMemoryEntry.state.in_(("provisional", "confirmed")),
        ).all():
            memory.state = "stale"
            memory.updated_at = now
    source.last_refreshed_at = now
    source.last_processed_at = now
    return {
        "changed": changed,
        "matched_records": len(rows),
        "cursor": cursor,
        "version_id": version.id if version else None,
    }

