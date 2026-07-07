"""Resolve the most recent explicit Bible reference from a Quest conversation."""

from __future__ import annotations


def recent_bible_reference(quest_id: str, *, limit: int = 24) -> str | None:
    """Return the newest canonical reference mentioned in a Quest conversation.

    This is intentionally used only for an explicit follow-up action such as
    "go ahead and request it" or "remember this". It never guesses a book or
    searches the web.
    """
    from core.database import ChatMessage, SessionLocal
    from services.bible_lookup import parse_bible_reference

    db = SessionLocal()
    try:
        rows = db.query(ChatMessage.content).filter(
            ChatMessage.session_id == quest_id,
            ChatMessage.role.in_(("user", "assistant")),
        ).order_by(ChatMessage.timestamp.desc()).limit(max(1, min(limit, 50))).all()
        for (content,) in rows:
            parsed = parse_bible_reference(content or "")
            if not parsed:
                continue
            if parsed.start_verse is None:
                continue
            end = parsed.end_verse or parsed.start_verse
            return f"{parsed.book_name} {parsed.chapter}:{parsed.start_verse}-{end}" if end != parsed.start_verse else f"{parsed.book_name} {parsed.chapter}:{parsed.start_verse}"
        return None
    finally:
        db.close()
