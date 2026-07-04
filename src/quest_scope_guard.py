"""Scope guard preventing accidental web substitution for Quest Bible evidence."""

from __future__ import annotations

import re


_BIBLE_TERMS = re.compile(
    r"\b(?:bible|scripture|verse|gospel|testament|john|matthew|mark|luke|acts|romans|"
    r"genesis|exodus|psalm|psalms|revelation|word of god|word was god|jesus|baptist)\b",
    re.I,
)
_EXTERNAL_TERMS = re.compile(
    r"\b(?:web|internet|online|browser|external|outside (?:the )?quest|bible gateway|biblehub)\b|https?://",
    re.I,
)


def block_external_bible_lookup(*, session_id: str | None, owner: str | None, request_text: str) -> str | None:
    """Return a bounded retry instruction when a Quest Bible source owns scope."""
    if not session_id or not _BIBLE_TERMS.search(request_text or ""):
        return None
    if _EXTERNAL_TERMS.search(request_text or ""):
        return None
    try:
        from core.database import QuestSource, SessionLocal
        from src.venture_auth import get_quest_role

        role = get_quest_role(owner, session_id)
        if role not in {"captain", "shipmate"}:
            return None
        db = SessionLocal()
        try:
            source = db.query(QuestSource.id).filter(
                QuestSource.session_id == session_id,
                QuestSource.source_type == "bible",
                QuestSource.status != "archived",
            ).first()
            if not source:
                return None
        finally:
            db.close()
    except Exception:
        return None
    return (
        "This active Quest has a scoped Bible source. External web lookup is blocked so web passages "
        "cannot be presented as Quest evidence. Use manage_quest_session with search_bible, "
        "retrieve_bible_passage, or request_bible_passage instead."
    )
