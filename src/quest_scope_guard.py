"""Scope guard preventing accidental web substitution for Quest Bible evidence."""

from __future__ import annotations

import re


_BIBLE_TERMS = re.compile(
    r"\b(?:bible|scripture|verse|gospel|testament|john|matthew|mark|luke|acts|romans|"
    r"genesis|exodus|psalm|psalms|revelation|word of god|word was god|logos|jesus|baptist|"
    r"jhn|mat|mrk|luk|rev)\b",
    re.I,
)
_BIBLE_URL = re.compile(r"(?:biblegateway|biblehub|bible\.com|ebible\.org|biblestudytools)\.", re.I)
_EXPLICIT_EXTERNAL_REQUEST = re.compile(
    r"\b(?:search (?:the )?web|search (?:the )?internet|look (?:it )?up online|"
    r"outside (?:the )?quest|external source|use bible gateway)\b",
    re.I,
)


def block_external_bible_lookup(*, session_id: str | None, owner: str | None, request_text: str) -> str | None:
    """Return a bounded retry instruction when a Quest Bible source owns scope.

    A known external Bible URL is always blocked inside an active Quest unless a
    caller uses an explicit opt-out route; tool-generated URLs are not evidence
    of a Captain request to leave Quest scope.
    """
    text = request_text or ""
    is_bible_request = bool(_BIBLE_TERMS.search(text) or _BIBLE_URL.search(text))
    if not session_id or not is_bible_request:
        return None
    if _EXPLICIT_EXTERNAL_REQUEST.search(text) and not _BIBLE_URL.search(text):
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
