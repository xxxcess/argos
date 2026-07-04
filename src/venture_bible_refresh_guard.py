"""Guard rails for Bible-source indexing.

Bible sources are a collection of separately selected books. The generic
``refresh_source`` path used to enqueue a source-wide job with no book scope;
the Bible adapter correctly rejected it as ``bible_scope_invalid``. This module
converts generic refreshes into a selected-book state check, reserves full work
for explicit reindex/retry actions, and repairs obsolete unscoped jobs without
hiding genuine book failures.
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
    """True only for a job with canonical Bible selection scope."""
    scope = _loads(getattr(job, "scope_json", None), {})
    return (
        isinstance(scope, dict)
        and scope.get("kind") == "bible_book_import"
        and bool(scope.get("book_id"))
        and bool(scope.get("selection_id"))
    )


def is_obsolete_unscoped_bible_job(job: Any) -> bool:
    """Identify legacy generic Bible refreshes, not genuine selected-book failures."""
    if is_book_scoped_job(job):
        return False
    return (
        str(getattr(job, "error_code", "") or "") == "bible_scope_invalid"
        or str(getattr(job, "status", "") or "") in {"queued", "running"}
    )


def _active_book_job(db, source):
    from core.database import QuestIndexJob

    rows = db.query(QuestIndexJob).filter(
        QuestIndexJob.source_id == source.id,
        QuestIndexJob.status.in_(("queued", "running")),
    ).order_by(QuestIndexJob.requested_at.asc()).all()
    return next((job for job in rows if is_book_scoped_job(job)), None)


def _latest_book_job(db, source):
    from core.database import QuestIndexJob

    rows = db.query(QuestIndexJob).filter(
        QuestIndexJob.source_id == source.id,
    ).order_by(QuestIndexJob.requested_at.desc()).limit(20).all()
    return next((job for job in rows if is_book_scoped_job(job)), None)


def _selected_books(db, source):
    from core.database import QuestBibleBookSelection

    return db.query(QuestBibleBookSelection).filter(
        QuestBibleBookSelection.source_id == source.id,
        QuestBibleBookSelection.active == True,  # noqa: E712
    ).order_by(QuestBibleBookSelection.canonical_order.asc()).all()


def _selection_state(db, source) -> str:
    selections = _selected_books(db, source)
    active = _active_book_job(db, source)
    states = {str(row.state or "") for row in selections}
    if (active and active.status == "running") or "indexing" in states:
        return "indexing"
    if (active and active.status == "queued") or "queued" in states:
        return "queued"
    if "failed" in states:
        return "partial" if "indexed" in states else "failed"
    if "paused" in states:
        return "paused"
    if "indexed" in states:
        return "ready"
    return "not_indexed"


def reconcile_bible_source_state(db, source) -> bool:
    """Cancel obsolete generic jobs and restore selected-book truth.

    A valid selected-book failure remains a failure and still appears in Needs
    attention. Only impossible source-wide Bible jobs are repaired.
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


def refresh_selected_bible_source(db, source):
    """Return current selected-book work without reimporting an indexed Bible.

    Bible sources are static snapshots by translation. A regular refresh is a
    status/reconciliation operation; it should not redownload all chapters just
    because a model used the generic refresh action.
    """
    if getattr(source, "source_type", None) != "bible":
        raise ValueError("not_a_bible_source")
    if getattr(source, "status", None) != "active":
        raise ValueError("bible_source_inactive")

    reconcile_bible_source_state(db, source)
    selections = _selected_books(db, source)
    if not selections:
        raise ValueError("bible_book_scope_required")
    active = _active_book_job(db, source)
    if active:
        return active, False

    # When a book has not completed, hand off only the first pending/failed
    # selection. An explicit reindex remains the path that resets all books.
    pending = next((row for row in selections if row.state != "indexed"), None)
    if pending:
        return queue_selected_bible_reindex(
            db,
            source,
            captain=str(getattr(source, "captain_username", "") or ""),
            selection_ids={pending.id},
        )
    return _latest_book_job(db, source), False


def queue_selected_bible_reindex(db, source, *, captain: str, selection_ids: set[str] | None = None):
    """Reindex selected books one at a time using valid book-scoped work."""
    from src.quest_indexing import enqueue_bible_book_job

    if getattr(source, "source_type", None) != "bible":
        raise ValueError("not_a_bible_source")
    if getattr(source, "status", None) != "active":
        raise ValueError("bible_source_inactive")

    reconcile_bible_source_state(db, source)
    current = _active_book_job(db, source)
    if current:
        return current, False

    selections = _selected_books(db, source)
    if not selections:
        raise ValueError("bible_book_scope_required")
    targets = [row for row in selections if selection_ids is None or row.id in selection_ids]
    if not targets:
        raise ValueError("bible_book_scope_required")

    for selection in targets:
        selection.state = "queued"
        selection.completed_chapters = 0
        selection.last_error = None
    first = targets[0]
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
        # Generic callers are refresh-like. Do not turn a fully indexed static
        # Bible source into an expensive full-Testament or full-book reimport.
        if getattr(source, "source_type", None) == "bible":
            parsed_scope = scope if isinstance(scope, dict) else _loads(scope, {})
            if parsed_scope.get("kind") != "bible_book_import":
                job, _ = refresh_selected_bible_source(db, source)
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
            from core.database import QuestSource, SessionLocal

            db = SessionLocal()
            try:
                current = db.query(QuestSource).filter(QuestSource.id == row.id).first()
                if current and reconcile_bible_source_state(db, current):
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
