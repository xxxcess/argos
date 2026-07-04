"""Guard Rails for Bible-source indexing.

Bible sources are a collection of separately selected books.  The generic
``refresh_source`` path used to enqueue a source-wide job with no book scope;
the Bible adapter correctly rejected it as ``bible_scope_invalid``.  This
module converts generic refresh/reindex/retry requests into selected-book work
and repairs obsolete unscoped jobs without hiding genuine book failures.
"""

from __future__ import annotations

import json
from typing import Any


def _loads(value: str | None, fallback: Any = None) -> Any:
    try:
        parsed = json.loads(value or "")
        return parsed if parsed is not None else ({} if fallback is None else fallback)
    except Exception:
        return {} if fallback is None else fallback


def is_book_scoped_job(job: Any) -> bool:
    """True only for a job that has a canonical Bible book selection scope."""
    scope = _loads(getattr(job, "scope_json", None), {})
    return (
        isinstance(scope, dict)
        and scope.get("kind") == "bible_book_import"
        and bool(scope.get("book_id"))
        and bool(scope.get("selection_id"))
    )


def is_obsolete_unscoped_bible_job(job: Any) -> bool:
    """Identify legacy generic Bible refreshes, not genuine book failures."""
    if is_book_scoped_job(job):
        return False
    return (
        str(getattr(job, "error_code", "") or "") == "bible_scope_invalid"
        or str(getattr(job, "status", "") or "") in {"queued", "running"}
    )


def _selection_state(db, source) -> str:
    from core.database import QuestBibleBookSelection, QuestIndexJob

    selections = db.query(QuestBibleBookSelection).filter(
        QuestBibleBookSelection.source_id == source.id,
        QuestBibleBookSelection.active == True,  # noqa: E712
    ).all()
    active_jobs = db.query(QuestIndexJob).filter(
        QuestIndexJob.source_id == source.id,
        QuestIndexJob.status.in_(("queued", "running")),
    ).all()
    book_job_states = [job.status for job in active_jobs if is_book_scoped_job(job)]
    states = {str(row.state or "") for row in selections}
    if "running" in book_job_states or "indexing" in states:
        return "indexing"
    if "queued" in book_job_states or "queued" in states:
        return "queued"
    if "failed" in states:
        return "partial" if "indexed" in states else "failed"
    if "paused" in states:
        return "paused"
    if "indexed" in states:
        return "ready"
    return "not_indexed"


def reconcile_bible_source_state(db, source) -> bool:
    """Cancel only obsolete generic jobs and restore selected-book truth.

    Returns whether anything changed. A valid selected-book failure remains a
    failure and still appears in Needs attention.
    """
    from core.database import QuestIndexJob, utcnow_naive

    if getattr(source, "source_type", None) != "bible":
        return False
    changed = False
    candidates = db.query(QuestIndexJob).filter(
        QuestIndexJob.source_id == source.id,
        QuestIndexJob.status.in_(("queued", "running", "failed")),
    ).all()
    for job in candidates:
        if not is_obsolete_unscoped_bible_job(job):
            continue
        job.status = "cancelled"
        job.finished_at = job.finished_at or utcnow_naive()
        job.safe_error_code = "bible_refresh_requires_book_scope"
        job.safe_error_message = (
            "Ignored an obsolete generic Bible refresh. Selected Bible books are indexed separately."
        )
        changed = True
    desired = _selection_state(db, source)
    if getattr(source, "index_state", None) != desired:
        source.index_state = desired
        changed = True
    return changed


def queue_selected_bible_reindex(db, source, *, captain: str):
    """Reindex active selected books one at a time using valid book scopes."""
    from core.database import QuestBibleBookSelection, QuestIndexJob
    from src.quest_indexing import enqueue_bible_book_job

    if getattr(source, "source_type", None) != "bible":
        raise ValueError("not_a_bible_source")
    if getattr(source, "status", None) != "active":
        raise ValueError("bible_source_inactive")

    reconcile_bible_source_state(db, source)
    active = db.query(QuestIndexJob).filter(
        QuestIndexJob.source_id == source.id,
        QuestIndexJob.status.in_(("queued", "running")),
    ).order_by(QuestIndexJob.requested_at.asc()).all()
    current = next((job for job in active if is_book_scoped_job(job)), None)
    if current:
        return current, False

    selections = db.query(QuestBibleBookSelection).filter(
        QuestBibleBookSelection.source_id == source.id,
        QuestBibleBookSelection.active == True,  # noqa: E712
    ).order_by(QuestBibleBookSelection.canonical_order.asc()).all()
    if not selections:
        raise ValueError("bible_book_scope_required")

    # A Bible source is immutable for a translation. "Refresh" therefore means
    # an explicit selected-book reindex, never a source-wide fetch.
    for selection in selections:
        selection.state = "queued"
        selection.completed_chapters = 0
        selection.last_error = None
    first = selections[0]
    job = enqueue_bible_book_job(
        db,
        source,
        first,
        created_by=captain or source.captain_username,
        selection_mode="reindex",
        priority=80,
    )
    source.index_state = "queued"
    return job, True


def recover_obsolete_bible_jobs() -> int:
    """Startup-safe reconciliation for installs that already contain bad jobs."""
    from core.database import QuestSource, SessionLocal

    db = SessionLocal()
    try:
        sources = db.query(QuestSource).filter(
            QuestSource.source_type == "bible",
            QuestSource.status != "archived",
        ).all()
        changed = sum(1 for source in sources if reconcile_bible_source_state(db, source))
        if changed:
            db.commit()
        return changed
    except Exception:
        db.rollback()
        return 0
    finally:
        db.close()


def install_bible_refresh_guard() -> None:
    """Patch generic enqueueing and status reads once during Venture startup."""
    import src.quest_indexing as indexing
    import routes.venture_routes_legacy as legacy

    if getattr(indexing, "_bible_refresh_guard_installed", False):
        return

    original_enqueue = indexing.enqueue_index_job
    original_claim_next = indexing._claim_next_job
    original_status = legacy._source_index_status

    def enqueue_index_job(db, source, *, trigger="manual_refresh", created_by=None, priority=100, scope=None):
        # Every generic caller becomes safe for Bible sources, including legacy
        # reindex/retry routes and agent tools that still use refresh_source.
        if getattr(source, "source_type", None) == "bible":
            parsed_scope = scope if isinstance(scope, dict) else _loads(scope, {})
            if parsed_scope.get("kind") != "bible_book_import":
                job, _ = queue_selected_bible_reindex(
                    db,
                    source,
                    captain=str(created_by or source.captain_username or ""),
                )
                return job
        return original_enqueue(
            db,
            source,
            trigger=trigger,
            created_by=created_by,
            priority=priority,
            scope=scope,
        )

    def claim_next_job():
        # Reconcile before the worker considers queued rows so a historic generic
        # Bible refresh cannot be executed into a permanent false failure.
        recover_obsolete_bible_jobs()
        return original_claim_next()

    def source_index_status(row):
        if getattr(row, "source_type", None) == "bible":
            from core.database import SessionLocal
            db = SessionLocal()
            try:
                if reconcile_bible_source_state(db, row):
                    db.commit()
            except Exception:
                db.rollback()
            finally:
                db.close()
        return original_status(row)

    indexing.enqueue_index_job = enqueue_index_job
    indexing._claim_next_job = claim_next_job
    legacy._source_index_status = source_index_status
    indexing._bible_refresh_guard_installed = True
