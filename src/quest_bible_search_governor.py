"""Bounded SQL candidate selection for Quest Bible topic search."""

from __future__ import annotations

import re

from sqlalchemy import func, or_


def install_bounded_bible_search() -> None:
    import src.quest_bible_workflow as workflow

    if getattr(workflow, "_bounded_bible_search_installed", False):
        return

    def search_indexed_bible(db, *, quest_id: str, role: str | None, query: str, source_id: str | None = None, limit: int = 4):
        source, ambiguous = workflow._source_by_handle(db, quest_id, role, source_id)
        if ambiguous and source is None:
            return {"status": "ambiguous_source", "sources": [{"id": row.id, "display_name": row.display_name} for row in ambiguous]}
        sources = [source] if source else workflow._sources(db, quest_id, role)
        if not sources:
            return {"status": "no_bible_source"}

        raw_terms = [
            term for term in re.findall(r"[a-z]{3,}", (query or "").lower())
            if term not in {"what", "does", "according", "about", "with", "that", "this", "from", "book", "gospel", "say", "says"}
        ]
        # Keep theology-defining terms even when they are common stop words.
        for term in ("word", "god", "jesus", "john", "baptist", "truth"):
            if term in (query or "").lower() and term not in raw_terms:
                raw_terms.append(term)
        raw_terms = list(dict.fromkeys(raw_terms))[:6]

        candidates = []
        for bible_source in sources:
            cfg = workflow._loads(bible_source.configuration_json, {})
            translation = str(cfg.get("default_translation") or cfg.get("translation") or "web").lower()
            selections = db.query(workflow.QuestBibleBookSelection).filter(
                workflow.QuestBibleBookSelection.source_id == bible_source.id,
                workflow.QuestBibleBookSelection.translation == translation,
                workflow.QuestBibleBookSelection.active == True,  # noqa: E712
                workflow.QuestBibleBookSelection.state == "indexed",
            ).all()
            indexed_ids = [row.book_id for row in selections]
            if not indexed_ids:
                continue
            base = db.query(workflow.BibleVerse, workflow.BibleChapterCache).join(
                workflow.BibleChapterCache,
                workflow.BibleVerse.bible_chapter_id == workflow.BibleChapterCache.id,
            ).filter(
                workflow.BibleChapterCache.translation == translation,
                workflow.BibleChapterCache.book_id.in_(indexed_ids),
            )
            if raw_terms:
                terms_filter = or_(*[
                    func.lower(workflow.BibleVerse.text).like(f"%{term}%")
                    for term in raw_terms
                ])
                rows = base.filter(terms_filter).order_by(
                    workflow.BibleChapterCache.book_id,
                    workflow.BibleChapterCache.chapter_number,
                    workflow.BibleVerse.verse_number,
                ).limit(250).all()
            else:
                rows = base.order_by(
                    workflow.BibleChapterCache.book_id,
                    workflow.BibleChapterCache.chapter_number,
                    workflow.BibleVerse.verse_number,
                ).limit(100).all()
            for verse, chapter in rows:
                score = workflow._score_verse(verse.text or "", set(raw_terms), query)
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
        passages, seen = [], set()
        for candidate in candidates:
            key = (candidate["source"].id, candidate["book_id"], candidate["chapter"], candidate["verse"])
            if key in seen:
                continue
            seen.add(key)
            book = workflow.validate_book_in_testament(
                candidate["book_id"],
                workflow._loads(candidate["source"].configuration_json, {}).get("testament"),
            )
            passages.append({
                "source_id": candidate["source"].id,
                "source_name": candidate["source"].display_name,
                "reference": f"{book.name} {candidate['chapter']}:{candidate['verse']} ({candidate['translation'].upper()})",
                "book_id": candidate["book_id"],
                "book_name": book.name,
                "chapter": candidate["chapter"],
                "start_verse": candidate["verse"],
                "end_verse": candidate["verse"],
                "text": f"{candidate['verse']}. {candidate['text']}",
                "speaker_provenance": "Scripture text; do not attribute speech to Jesus unless the retrieved passage itself identifies direct speech.",
                "chunk_ids": workflow._passage_chunks(
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

    workflow.search_indexed_bible = search_indexed_bible
    workflow._bounded_bible_search_installed = True
