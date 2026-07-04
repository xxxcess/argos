"""Small runtime patches that remove remaining high-churn Venture reads."""

from __future__ import annotations

from sqlalchemy import and_, func, or_


def install_route_performance_patch() -> None:
    import routes.venture_routes_optimized as optimized

    if getattr(optimized, "_performance_patch_installed", False):
        return
    legacy = optimized._legacy
    original_management_snapshot = optimized._management_snapshot

    def visible_memory_query(db, quest_id: str, role: str):
        query = db.query(legacy.QuestMemoryEntry).filter(
            legacy.QuestMemoryEntry.session_id == quest_id,
            legacy.QuestMemoryEntry.state != "retired",
        )
        if role != "captain":
            query = query.filter(legacy.QuestMemoryEntry.visibility == "quest_shared")
        # New Artifact-derived memory always has a semantic title/revelation.
        # Keep manual memory untouched while filtering obvious historic evidence
        # dumps in SQL, so a rail count never hydrates the whole ledger.
        generated_quality = and_(
            func.length(legacy.QuestMemoryEntry.title) >= 14,
            func.length(legacy.QuestMemoryEntry.content) >= 24,
            func.lower(legacy.QuestMemoryEntry.title).notin_(
                ("insight", "insights", "summary", "artifact", "quest artifact", "1")
            ),
        )
        return query.filter(or_(
            legacy.QuestMemoryEntry.artifact_id == None,  # noqa: E711
            legacy.QuestMemoryEntry.created_by != "argo",
            generated_quality,
        ))

    def visible_memory_count(db, quest_id: str, role: str) -> int:
        return int(visible_memory_query(db, quest_id, role).with_entities(
            func.count(legacy.QuestMemoryEntry.id)
        ).scalar() or 0)

    def visible_memory_page(db, quest_id: str, role: str, limit: int):
        # Fetch a bounded multiple instead of the full ledger. This absorbs a
        # small cluster of legacy rows that fail the stricter Python quality
        # test without letting them hide later valid revelations.
        fetch_limit = min(max(limit * 4, limit + 1), 400)
        rows = visible_memory_query(db, quest_id, role).order_by(
            legacy.QuestMemoryEntry.pinned.desc(),
            legacy.QuestMemoryEntry.updated_at.desc(),
        ).limit(fetch_limit).all()
        visible = [row for row in rows if optimized.is_displayable_memory(row)]
        return visible[:limit], len(rows) == fetch_limit

    def detailed_source_payload(db, source, role: str) -> dict:
        key = ("venture-source-status", source.session_id, role)
        cache = db.info.get(key)
        if cache is None:
            compact = optimized._compact_source_payload(db, source.session_id, role)
            cache = {row["id"]: row["index_status"] for row in compact["sources"]}
            db.info[key] = cache
        status = dict(cache.get(source.id) or {"index_state": "not_indexed"})
        config = legacy._json_loads(source.configuration_json, {})
        if role != "captain" and source.access_mode == "shared_summaries":
            config = {"summary": config.get("summary", "") if isinstance(config, dict) else ""}
        if source.source_type == "bible":
            selections = db.query(legacy.QuestBibleBookSelection).filter(
                legacy.QuestBibleBookSelection.source_id == source.id,
                legacy.QuestBibleBookSelection.active == True,  # noqa: E712
            ).order_by(legacy.QuestBibleBookSelection.canonical_order.asc()).all()
            job_states = legacy._bible_book_job_states(db, source.id)
            status["books"] = [
                legacy._selection_to_dict(
                    selection,
                    job_states.get(selection.book_id)
                    if selection.state in {"queued", "indexing", "paused", "failed"}
                    else None,
                )
                for selection in selections
            ]
        return {
            "id": source.id,
            "session_id": source.session_id,
            "source_type": source.source_type,
            "source_mode": source.source_mode,
            "refresh_strategy": getattr(source, "refresh_strategy", None) or "manual",
            "display_name": source.display_name,
            "access_mode": source.access_mode,
            "configuration": config,
            "status": source.status,
            "index_state": status.get("index_state"),
            "index_status": status,
            "last_refreshed_at": source.last_refreshed_at.isoformat() + "Z" if source.last_refreshed_at else None,
            "last_processed_at": source.last_processed_at.isoformat() + "Z" if source.last_processed_at else None,
        }

    def management_snapshot(db, quest_id: str, role: str) -> dict:
        snapshot = original_management_snapshot(db, quest_id, role)
        if role == "captain":
            from src.venture_runtime_metrics import process_pressure_snapshot
            snapshot["runtime"] = process_pressure_snapshot()
        return snapshot

    optimized._visible_memory_count = visible_memory_count
    optimized._visible_memory_page = visible_memory_page
    optimized._detailed_source_payload = detailed_source_payload
    optimized._management_snapshot = management_snapshot
    optimized._performance_patch_installed = True
