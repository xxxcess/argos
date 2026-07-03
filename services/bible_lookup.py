"""Local exact Bible reference parsing and indexed lookup."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from core.database import BibleChapterCache, BibleVerse, QuestBibleBookSelection, QuestSource
from src.bible_catalog import resolve_book_alias


@dataclass(frozen=True)
class ParsedBibleReference:
    book_id: str
    book_name: str
    chapter: int
    start_verse: int | None = None
    end_verse: int | None = None


_REF_RE = re.compile(r"\b((?:[123]|I{1,3}|First|Second|Third)?\s*[A-Za-z][A-Za-z ]+?)\s+(\d{1,3})(?::(\d{1,3})(?:\s*[-–]\s*(\d{1,3}))?)?\b", re.I)


def parse_bible_reference(text: str) -> ParsedBibleReference | None:
    for match in _REF_RE.finditer(text or ""):
        book = resolve_book_alias(match.group(1).strip())
        if not book:
            continue
        chapter = int(match.group(2))
        start = int(match.group(3)) if match.group(3) else None
        end = int(match.group(4)) if match.group(4) else start
        if book.chapters == 1 and start is None:
            start = chapter
            end = chapter
            chapter = 1
        if chapter < 1 or chapter > book.chapters:
            continue
        return ParsedBibleReference(book.book_id, book.name, chapter, start, end)
    return None


def _reference(book_name: str, chapter: int, start: int | None, end: int | None, translation: str) -> str:
    if start is None:
        return f"{book_name} {chapter} ({translation.upper()})"
    if end and end != start:
        return f"{book_name} {chapter}:{start}-{end} ({translation.upper()})"
    return f"{book_name} {chapter}:{start} ({translation.upper()})"


def resolve_indexed_bible_reference(db, *, quest_id: str, source: QuestSource, reference: str) -> dict[str, Any]:
    parsed = parse_bible_reference(reference)
    if not parsed:
        return {"status": "invalid_reference", "reference": reference}
    cfg = {}
    try:
        import json
        cfg = json.loads(source.configuration_json or "{}")
    except Exception:
        cfg = {}
    translation = str(cfg.get("default_translation") or cfg.get("translation") or "web").lower()
    selected = db.query(QuestBibleBookSelection).filter(
        QuestBibleBookSelection.source_id == source.id,
        QuestBibleBookSelection.translation == translation,
        QuestBibleBookSelection.book_id == parsed.book_id,
        QuestBibleBookSelection.active == True,  # noqa: E712
    ).first()
    if not selected:
        return {"status": "not_indexed", "parsed": parsed.__dict__}
    if selected.state in {"queued", "indexing", "paused"}:
        return {"status": "pending_index", "selection": _selection_dict(selected), "parsed": parsed.__dict__}
    if selected.state != "indexed":
        return {"status": selected.state, "selection": _selection_dict(selected), "parsed": parsed.__dict__}
    chapter = db.query(BibleChapterCache).filter(
        BibleChapterCache.translation == translation,
        BibleChapterCache.book_id == parsed.book_id,
        BibleChapterCache.chapter_number == parsed.chapter,
    ).first()
    if not chapter:
        return {"status": "not_indexed", "selection": _selection_dict(selected), "parsed": parsed.__dict__}
    q = db.query(BibleVerse).filter(BibleVerse.bible_chapter_id == chapter.id)
    if parsed.start_verse:
        q = q.filter(BibleVerse.verse_number >= parsed.start_verse, BibleVerse.verse_number <= (parsed.end_verse or parsed.start_verse))
    verses = q.order_by(BibleVerse.verse_number.asc()).all()
    if not verses:
        return {"status": "not_indexed", "selection": _selection_dict(selected), "parsed": parsed.__dict__}
    text = " ".join(f"{v.verse_number}. {v.text}" for v in verses)
    start = verses[0].verse_number
    end = verses[-1].verse_number
    return {
        "status": "indexed",
        "reference": _reference(parsed.book_name, parsed.chapter, start, end, translation),
        "translation": translation,
        "book_id": parsed.book_id,
        "book_name": parsed.book_name,
        "chapter": parsed.chapter,
        "start_verse": start,
        "end_verse": end,
        "text": text,
        "selection": _selection_dict(selected),
    }


def _selection_dict(row: QuestBibleBookSelection) -> dict[str, Any]:
    return {
        "id": row.id,
        "book_id": row.book_id,
        "book_name": row.book_name,
        "state": row.state,
        "completed_chapters": row.completed_chapters,
        "total_chapters": row.total_chapters,
        "last_error": row.last_error,
    }
