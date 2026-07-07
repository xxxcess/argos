"""Idempotent indexes for Venture's high-frequency queue and panel queries."""

from __future__ import annotations

from core.database import engine


_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_quest_synthesis_quest_trigger_status "
    "ON quest_synthesis_jobs(quest_id, trigger, status, requested_at)",
    "CREATE INDEX IF NOT EXISTS ix_quest_synthesis_artifact_revision_status "
    "ON quest_synthesis_jobs(quest_id, artifact_proposal_id, status, requested_at)",
    "CREATE INDEX IF NOT EXISTS ix_quest_memory_panel "
    "ON quest_memory_entries(session_id, state, visibility, updated_at)",
    "CREATE INDEX IF NOT EXISTS ix_quest_index_source_recent "
    "ON quest_index_jobs(source_id, requested_at)",
    "CREATE INDEX IF NOT EXISTS ix_quest_chunks_source_current "
    "ON quest_evidence_chunks(source_id, is_current)",
    "CREATE INDEX IF NOT EXISTS ix_quest_bible_selection_summary "
    "ON quest_bible_book_selections(source_id, active, state)",
    "CREATE INDEX IF NOT EXISTS ix_quest_artifact_panel "
    "ON quest_artifact_proposals(session_id, status, published_at)",
    "CREATE INDEX IF NOT EXISTS ix_documents_quest_panel "
    "ON documents(session_id, owner, archived, is_active, updated_at)",
)


def ensure_venture_indexes() -> None:
    """Create missing indexes without schema rewrites or table locks beyond DDL."""
    try:
        with engine.begin() as connection:
            for statement in _INDEXES:
                connection.exec_driver_sql(statement)
    except Exception:
        # Startup must remain available for deployments with managed-schema
        # permissions; missing indexes reduce efficiency but cannot affect data.
        return
