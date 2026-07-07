"""Deterministic Bible retrieval and request workflow for Venture Quests.

This module deliberately stays inside a Quest's selected Bible sources.  It
never performs a web lookup and never turns an unindexed passage into a claim.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from core.database import (
    BibleChapterCache,
    BibleVerse,
    QuestBibleBookSelection,
    QuestEvidenceChunk,
    QuestRetrievalRun,
    QuestSource,
    Session as DbSession,
    SessionLocal,
    utcnow_naive,
)
from src.bible_catalog import validate_book_in_testament
from src.venture_auth import get_quest_role


_STOP_WORDS = {
    "a", "an", "and", "are", "according", "about", "book", "does", "for", "god",
    "gospel", "how", "in", "is", "it", "john", "of", "say", "says", "the", "to",
    "what", "who", "word", "words", "would", "you",
}


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        parsed = json.loads(value or "")
        return parsed if parsed is not None else fallback
    except Exception:
        return fallback


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True)


def _source_readable(source: QuestSource, role: str | None) -> bool:
    return role == "captain" or (role == "shipmate" and source.access_mode == "shared_read")


def _sources(db, quest_id: str, role: str | None) -> list[QuestSource]:
    rows = db.query(QuestSource).filter(
        QuestSource.session_id == quest_id,
        QuestSource.source_type == "bible",
        QuestSource.status != "archived",
    ).all()
    return [row for row in rows if _source_readable(row, role)]


def _source_by_handle(db, quest_id: str, role: str | None, source_id: str | None) -> tuple[QuestSource | None, list[QuestSource]]:
    rows = _sources(db, quest_id, role)
    handle = str(source_id or "").strip()
    if not handle:
        return (rows[0] if len(rows) == 1 else None), rows
    exact = next((row for row in rows if row.id == handle), None)
    if exact:
        return exact, []
    matches = [row for row in rows if row.id.startswith(handle)]
    return (matches[0] if len(matches) == 1 else None), matches


def _display_source(row: QuestSource) -> str:
    return f"{row.display_name} (id={row.id}, handle={row.id[:8]})"


def _selection(db, source: QuestSource, book_id: str) -> QuestBibleBookSelection | None:
    config = _loads(source.configuration_json, {})
    translation = str(config.get("default_translation") or config.get("translation") or "web").lower()
    return db.query(QuestBibleBookSelection).filter(
        QuestBibleBookSelection.source_id == source.id,
        QuestBibleBookSelection.translation == translation,
        QuestBibleBookSelection.book_id == book_id,
        QuestBibleBookSelection.active == True,  # noqa: E712
    ).first()


def _passage_chunks(db, quest_id: str, source: QuestSource, *, book_name: str, chapter: int, start: int, end: int) -> list[str]:
    rows = db.query(QuestEvidenceChunk).filter(
        QuestEvidenceChunk.quest_id == quest_id,
        QuestEvidenceChunk.source_id == source.id,
        QuestEvidenceChunk.is_current == True,  # noqa: E712
        QuestEvidenceChunk.locator.like(f"{book_name} {chapter}:%"),
    ).all()
    matched: list[str] = []
    locator_re = re.compile(r":(\d+)(?:-(\d+))?\s*\(", re.I)
    for row in rows:
        match = locator_re.search(row.locator or "")
        if not match:
            continue
        left = int(match.group(1))
        right = int(match.group(2) or left)
        if left <= end and right >= start:
            matched.append(row.id)
    return matched[:6]


def _exact_passage(db, *, quest_id: str, source: QuestSource, reference: str) -> dict[str, Any]:
    from services.bible_lookup import resolve_indexed_bible_reference

    result = resolve_indexed_bible_reference(db, quest_id=quest_id, source=source, reference=reference)
    if result.get("status") == "indexed":
        result["source_id"] = source.id
        result["source_name"] = source.display_name
        result["chunk_ids"] = _passage_chunks(
            db,
            quest_id,
            source,
            book_name=result["book_name"],
            chapter=int(result["chapter"]),
            start=int(result["start_verse"]),
            end=int(result["end_verse"]),
        )
    return result


def _topic_tokens(query: str) -> set[str]:
    return {
        token for token in re.findall(r"[a-z]{3,}", (query or "").lower())
        if token not in _STOP_WORDS
    }


def _score_verse(text: str, tokens: set[str], query: str) -> float:
    lower = (text or "").lower()
    score = float(sum(1 for token in tokens if token in lower))
    asked = (query or "").lower()
    if "word" in asked and "god" in asked:
        if "word was god" in lower:
            score += 18
        if "word became flesh" in lower:
            score += 14
        if "word" in lower and "god" in lower:
            score += 8
    if "john the baptist" in asked or "baptist" in asked:
        if "john" in lower or "baptist" in lower:
            score += 4
    return score


def search_indexed_bible(db, *, quest_id: str, role: str | None, query: str, source_id: str | None = None, limit: int = 4) -> dict[str, Any]:
    """Return small, Quest-owned Bible passages for a natural-language query."""
    source, ambiguous = _source_by_handle(db, quest_id, role, source_id)
    if ambiguous and source is None:
        return {
            "status": "ambiguous_source",
            "sources": [{"id": row.id, "display_name": row.display_name} for row in ambiguous],
        }
    sources = [source] if source else _sources(db, quest_id, role)
    if not sources:
        return {"status": "no_bible_source"}

    tokens = _topic_tokens(query)
    candidates: list[dict[str, Any]] = []
    for bible_source in sources:
        cfg = _loads(bible_source.configuration_json, {})
        translation = str(cfg.get("default_translation") or cfg.get("translation") or "web").lower()
        selections = db.query(QuestBibleBookSelection).filter(
            QuestBibleBookSelection.source_id == bible_source.id,
            QuestBibleBookSelection.translation == translation,
            QuestBibleBookSelection.active == True,  # noqa: E712
            QuestBibleBookSelection.state == "indexed",
        ).all()
        indexed_ids = [row.book_id for row in selections]
        if not indexed_ids:
            continue
        rows = db.query(BibleVerse, BibleChapterCache).join(
            BibleChapterCache,
            BibleVerse.bible_chapter_id == BibleChapterCache.id,
        ).filter(
            BibleChapterCache.translation == translation,
            BibleChapterCache.book_id.in_(indexed_ids),
        ).all()
        for verse, chapter in rows:
            score = _score_verse(verse.text or "", tokens, query)
            if score <= 0:
                continue
            candidates.append({
                "score": score,
                "source": bible_source,
                "translation": translation,
                "book_id": chapter.book_id,
                "chapter": chapter.chapter_number,
                "verse": verse.verse_number,
                "text": verse.text or "",
            })

    if not candidates:
        return {"status": "no_indexed_match"}
    candidates.sort(key=lambda item: (-item["score"], item["book_id"], item["chapter"], item["verse"]))

    passages: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, int]] = set()
    for candidate in candidates:
        key = (candidate["source"].id, candidate["book_id"], candidate["chapter"], candidate["verse"])
        if key in seen:
            continue
        seen.add(key)
        book = validate_book_in_testament(candidate["book_id"], _loads(candidate["source"].configuration_json, {}).get("testament"))
        reference = f"{book.name} {candidate['chapter']}:{candidate['verse']} ({candidate['translation'].upper()})"
        passages.append({
            "source_id": candidate["source"].id,
            "source_name": candidate["source"].display_name,
            "reference": reference,
            "book_id": candidate["book_id"],
            "book_name": book.name,
            "chapter": candidate["chapter"],
            "start_verse": candidate["verse"],
            "end_verse": candidate["verse"],
            "text": f"{candidate['verse']}. {candidate['text']}",
            "speaker_provenance": "Scripture text; do not attribute speech to Jesus unless the retrieved passage itself identifies direct speech.",
            "chunk_ids": _passage_chunks(
                db,
                quest_id,
                candidate["source"],
                book_name=book.name,
                chapter=int(candidate["chapter"]),
                start=int(candidate["verse"]),
                end=int(candidate["verse"]),
            ),
        })
        if len(passages) >= max(1, min(limit, 6)):
            break
    return {"status": "indexed", "passages": passages}


def _render_passages(passages: list[dict[str, Any]]) -> str:
    lines = ["QUEST BIBLE EVIDENCE — retrieved only from the selected Quest source."]
    for index, passage in enumerate(passages, 1):
        lines.extend([
            f"[{index}] {passage['source_name']} | {passage['reference']}",
            passage["text"],
            f"Provenance: {passage['speaker_provenance']}",
        ])
    lines.append("Do not substitute web results for these passages or treat the provenance note as a claim about who is speaking.")
    return "\n".join(lines)


def _make_retrieval_run(db, *, quest_id: str, requester: str, query: str, passages: list[dict[str, Any]]) -> str:
    chunk_ids = [chunk_id for item in passages for chunk_id in item.get("chunk_ids", [])]
    run = QuestRetrievalRun(
        id=uuid.uuid4().hex,
        quest_id=quest_id,
        session_id=quest_id,
        requester=requester,
        retrieved_at=utcnow_naive(),
        query_hash=hashlib.sha256((query or "").encode("utf-8")).hexdigest(),
        retrieval_mode="exact_bible_reference",
        freshness_requested=False,
        source_ids_json=_dumps(sorted({item["source_id"] for item in passages})),
        chunk_ids_json=_dumps(chunk_ids),
        live_verified_ids_json=_dumps([]),
    )
    db.add(run)
    db.flush()
    return run.id


def _queue_book_for_reference(db, *, quest_id: str, captain: str, source: QuestSource, reference: str) -> dict[str, Any]:
    from routes.venture_routes_legacy import BibleBooksPayload, _queue_bible_books
    from services.bible_lookup import parse_bible_reference

    parsed = parse_bible_reference(reference)
    if not parsed:
        return {"status": "invalid_reference", "message": "Provide a canonical reference such as John 1:1-14."}
    config = _loads(source.configuration_json, {})
    testament = str(config.get("testament") or "").lower()
    try:
        validate_book_in_testament(parsed.book_id, testament)
    except Exception:
        return {
            "status": "outside_source_scope",
            "message": f"{parsed.book_name} is outside the selected {testament or 'current'} Testament source.",
        }
    selection = _selection(db, source, parsed.book_id)
    if selection and selection.state == "indexed":
        return {"status": "indexed", "message": f"{parsed.book_name} is already indexed; retrieve {reference} directly."}
    if selection and selection.state in {"queued", "indexing"}:
        return {
            "status": "pending_index",
            "message": f"{parsed.book_name} is already queued for Quest indexing ({selection.state}).",
            "selection_id": selection.id,
        }
    jobs = _queue_bible_books(
        db,
        source=source,
        captain=captain,
        payload=BibleBooksPayload(
            translation=str(config.get("default_translation") or "web"),
            selection_mode="manual_selection",
            books=[parsed.book_id],
        ),
    )
    return {
        "status": "queued",
        "message": f"Queued {parsed.book_name} for the selected Quest Bible source. Argo will retrieve {reference} from Quest evidence after indexing completes.",
        "job_ids": [job.id for job in jobs],
    }


def execute_quest_bible_action(content: str | dict[str, Any], *, owner: str | None, session_id: str | None) -> dict[str, Any]:
    """Agent-facing Quest Bible actions with strict source and role boundaries."""
    try:
        args = json.loads(content) if isinstance(content, str) else dict(content or {})
    except (TypeError, ValueError):
        return {"error": "Invalid JSON arguments", "exit_code": 1}
    if not isinstance(args, dict):
        return {"error": "Quest action arguments must be an object", "exit_code": 1}

    action = str(args.get("action") or "").strip().lower()
    aliases = {
        "lookup_bible": "retrieve_bible_passage",
        "retrieve_bible": "retrieve_bible_passage",
        "request_passage": "request_bible_passage",
        "remember_passage": "remember_bible_passage",
    }
    action = aliases.get(action, action)
    quest_id = str(args.get("quest_id") or session_id or "").strip()
    if not quest_id:
        return {"error": "Quest Bible actions require an active Quest session", "exit_code": 1}
    role = get_quest_role(owner, quest_id)
    if role not in {"captain", "shipmate"}:
        return {"error": "Quest membership is required.", "exit_code": 1}

    db = SessionLocal()
    try:
        quest = db.query(DbSession).filter(DbSession.id == quest_id).first()
        if not quest:
            return {"error": "Quest not found", "exit_code": 1}

        if action == "list_sources":
            rows = _sources(db, quest_id, role)
            output = "\n".join(f"- {_display_source(row)} status={row.status} index={row.index_state}" for row in rows)
            return {"output": output or "No readable Quest Bible source.", "sources": [{"id": row.id, "handle": row.id[:8], "display_name": row.display_name} for row in rows], "exit_code": 0}

        if action == "inspect_source":
            source, candidates = _source_by_handle(db, quest_id, role, args.get("source_id"))
            if source is None:
                if candidates:
                    return {"error": "Source handle is ambiguous. Use one full id.", "sources": [{"id": row.id, "display_name": row.display_name} for row in candidates], "exit_code": 1}
                return {"error": "Quest source not found. Use list_sources and pass the full id or its unique handle.", "exit_code": 1}
            cfg = _loads(source.configuration_json, {})
            books = db.query(QuestBibleBookSelection).filter(
                QuestBibleBookSelection.source_id == source.id,
                QuestBibleBookSelection.active == True,  # noqa: E712
            ).all()
            indexed = [row.book_name for row in books if row.state == "indexed"]
            pending = [row.book_name for row in books if row.state in {"queued", "indexing"}]
            return {
                "output": "\n".join([
                    f"Source: {_display_source(source)}",
                    f"Scope: {cfg.get('testament', 'unknown')} testament · {cfg.get('default_translation', 'web').upper()}",
                    f"Indexed books: {', '.join(indexed) or '(none)'}",
                    f"Pending books: {', '.join(pending) or '(none)'}",
                ]),
                "source": {"id": source.id, "handle": source.id[:8], "display_name": source.display_name},
                "exit_code": 0,
            }

        if action in {"retrieve_bible_passage", "request_bible_passage", "remember_bible_passage"}:
            reference = str(args.get("reference") or args.get("query") or "").strip()
            if not reference:
                return {"error": "reference is required, for example John 1:1-14.", "exit_code": 1}
            source, candidates = _source_by_handle(db, quest_id, role, args.get("source_id"))
            if source is None:
                if candidates:
                    return {"error": "Source handle is ambiguous. Use one full id.", "sources": [{"id": row.id, "display_name": row.display_name} for row in candidates], "exit_code": 1}
                return {"error": "No readable Quest Bible source was found.", "exit_code": 1}
            exact = _exact_passage(db, quest_id=quest_id, source=source, reference=reference)
            if exact.get("status") != "indexed":
                if action == "request_bible_passage":
                    if role != "captain":
                        return {"error": "Only the Captain can queue missing Bible books.", "exit_code": 1}
                    queued = _queue_book_for_reference(db, quest_id=quest_id, captain=str(owner or ""), source=source, reference=reference)
                    db.commit()
                    return {"output": queued["message"], "request": queued, "exit_code": 0 if queued["status"] in {"queued", "indexed", "pending_index"} else 1}
                status = exact.get("status") or "not_indexed"
                return {
                    "error": f"{reference} is not currently available in {source.display_name} ({status}). Use request_bible_passage to queue the selected book; do not use web search as a substitute for Quest evidence.",
                    "source": {"id": source.id, "display_name": source.display_name},
                    "exit_code": 1,
                }
            passage = {
                **exact,
                "speaker_provenance": "Scripture text; do not attribute speech to Jesus unless the retrieved passage itself identifies direct speech.",
            }
            if action == "retrieve_bible_passage":
                return {"output": _render_passages([passage]), "passages": [passage], "scope": "quest_only", "exit_code": 0}
            if action == "request_bible_passage":
                return {"output": _render_passages([passage]), "passages": [passage], "scope": "quest_only", "exit_code": 0}
            if role != "captain":
                return {"error": "Only the Captain can request an Artifact note from Quest evidence.", "exit_code": 1}
            run_id = _make_retrieval_run(db, quest_id=quest_id, requester=str(owner or ""), query=reference, passages=[passage])
            from src.venture_synthesis import enqueue_synthesis_job, synthesis_job_to_dict
            job = enqueue_synthesis_job(
                db,
                quest_id=quest_id,
                trigger="captain_requested",
                created_by=str(owner or ""),
                retrieval_run_id=run_id,
            )
            db.commit()
            return {
                "output": (
                    f"Queued an Artifact draft grounded in {passage['reference']}. "
                    "Review and publish that Artifact before Voyage Memory is created."
                ),
                "passages": [passage],
                "retrieval_run_id": run_id,
                "synthesis_job": synthesis_job_to_dict(job),
                "exit_code": 0,
            }

        if action == "search_bible":
            query = str(args.get("query") or "").strip()
            if not query:
                return {"error": "query is required for search_bible.", "exit_code": 1}
            from services.bible_lookup import parse_bible_reference
            if parse_bible_reference(query):
                args["action"] = "retrieve_bible_passage"
                return execute_quest_bible_action(args, owner=owner, session_id=quest_id)
            result = search_indexed_bible(db, quest_id=quest_id, role=role, query=query, source_id=args.get("source_id"))
            if result.get("status") != "indexed":
                return {
                    "error": "No matching passage was found in the indexed Quest Bible source. Use request_bible_passage with an explicit reference to add a missing book; do not substitute web results.",
                    "status": result.get("status"),
                    "exit_code": 1,
                }
            return {"output": _render_passages(result["passages"]), "passages": result["passages"], "scope": "quest_only", "exit_code": 0}

        return {"error": "Unsupported Quest Bible action.", "exit_code": 1}
    except Exception as exc:
        db.rollback()
        return {"error": f"Quest Bible action failed safely: {exc}", "exit_code": 1}
    finally:
        db.close()
