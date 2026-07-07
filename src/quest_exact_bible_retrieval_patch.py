"""Correct Quest retrieval for exact references and Bible-topic questions.

The older retrieval flow collected exact Bible results but returned early when
vector hits were empty, then attempted to persist a missing ``chunk_id``. This
patch gives deterministic Quest Bible evidence priority and persists only real
chunk ids.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid


def _dumps(value) -> str:
    return json.dumps(value if value is not None else [], sort_keys=True)


def _looks_bible_scoped(query: str) -> bool:
    return bool(re.search(
        r"\b(?:bible|scripture|verse|gospel|testament|john|matthew|mark|luke|acts|romans|"
        r"genesis|exodus|psalm|psalms|revelation|word of god|jesus|baptist)\b",
        query or "",
        re.I,
    ))


def install_exact_bible_retrieval() -> None:
    import src.quest_retrieval as retrieval

    if getattr(retrieval, "_exact_bible_retrieval_installed", False):
        return
    original = retrieval.retrieve_quest_evidence

    def scoped_retrieval(*, quest_id, requester, query, message_id_or_turn_id=None, limit=6, token_char_budget=6000):
        from core.database import QuestRetrievalRun, QuestSource, Session as DbSession, SessionLocal, utcnow_naive
        from services.bible_lookup import parse_bible_reference
        from src.quest_bible_workflow import _exact_passage, _sources, search_indexed_bible
        from src.venture_auth import get_quest_role

        if not _looks_bible_scoped(query or ""):
            return original(
                quest_id=quest_id,
                requester=requester,
                query=query,
                message_id_or_turn_id=message_id_or_turn_id,
                limit=limit,
                token_char_budget=token_char_budget,
            )
        db = SessionLocal()
        try:
            quest = db.query(DbSession).filter(DbSession.id == quest_id).first()
            role = get_quest_role(requester, quest_id)
            if not quest or role not in {"captain", "shipmate"}:
                return original(
                    quest_id=quest_id,
                    requester=requester,
                    query=query,
                    message_id_or_turn_id=message_id_or_turn_id,
                    limit=limit,
                    token_char_budget=token_char_budget,
                )
            sources = _sources(db, quest_id, role)
            if not sources:
                return original(
                    quest_id=quest_id,
                    requester=requester,
                    query=query,
                    message_id_or_turn_id=message_id_or_turn_id,
                    limit=limit,
                    token_char_budget=token_char_budget,
                )
            parsed = parse_bible_reference(query or "")
            passages = []
            if parsed:
                for source in sources:
                    found = _exact_passage(db, quest_id=quest_id, source=source, reference=query)
                    if found.get("status") == "indexed":
                        passages.append({
                            **found,
                            "speaker_provenance": "Scripture text; do not attribute speech to Jesus unless the passage itself identifies direct speech.",
                        })
                        if len(passages) >= 2:
                            break
                # An explicit reference is a scope request. Do not substitute a
                # semantic/vector match when the requested passage is unavailable.
                if not passages:
                    return retrieval.QuestRetrievalResult(
                        context=(
                            "[QUEST BIBLE STATUS]\n"
                            f"{query} is not indexed in the selected Quest Bible source. "
                            "Use manage_quest_session request_bible_passage to queue it; do not use web material as Quest evidence.\n"
                            "[/QUEST BIBLE STATUS]"
                        ),
                        recall=[],
                        retrieval_mode="exact_bible_reference_missing",
                    )
            else:
                topic = search_indexed_bible(
                    db,
                    quest_id=quest_id,
                    role=role,
                    query=query,
                    limit=min(4, max(1, limit)),
                )
                if topic.get("status") == "indexed":
                    passages = topic["passages"]
            if not passages:
                return original(
                    quest_id=quest_id,
                    requester=requester,
                    query=query,
                    message_id_or_turn_id=message_id_or_turn_id,
                    limit=limit,
                    token_char_budget=token_char_budget,
                )

            blocks = [
                "[QUEST BIBLE EVIDENCE - UNTRUSTED SOURCE MATERIAL]",
                "Use only these selected Quest passages. Do not replace them with web passages.",
                "Bible policy: quote or paraphrase only retrieved evidence; cite Book chapter:start-end and translation; distinguish quotation, interpretation, inference, and uncertainty. Use 'John records' when speaker attribution is not explicit.",
            ]
            recall = []
            used = 0
            for index, passage in enumerate(passages, 1):
                text = str(passage.get("text") or "")
                if used + len(text) > token_char_budget and recall:
                    break
                used += len(text)
                blocks.append(f"\n[{index}] {passage['source_name']} | {passage['reference']}\n{text}")
                recall.append({
                    "source_id": passage["source_id"],
                    "source_name": passage["source_name"],
                    "source_version_id": None,
                    "locator": passage["reference"],
                    "freshness_requested": False,
                    "retrieval_mode": "exact_bible_reference" if parsed else "indexed_bible_topic",
                    "extraction_method": "bible_lookup",
                    "excerpt": text[:280],
                    "access": "shared_read",
                    "timestamp": None,
                    "chunk_ids": passage.get("chunk_ids") or [],
                    "metadata": {
                        "translation": passage.get("translation"),
                        "book_id": passage.get("book_id"),
                        "book_name": passage.get("book_name"),
                        "chapter": passage.get("chapter"),
                        "start_verse": passage.get("start_verse"),
                        "end_verse": passage.get("end_verse"),
                        "reference": passage.get("reference"),
                        "speaker_provenance": passage.get("speaker_provenance"),
                    },
                })
            blocks.append("[/QUEST BIBLE EVIDENCE]")
            chunk_ids = [chunk_id for item in recall for chunk_id in item.get("chunk_ids", [])]
            run = QuestRetrievalRun(
                id=uuid.uuid4().hex,
                quest_id=quest_id,
                session_id=quest_id,
                message_id_or_turn_id=message_id_or_turn_id,
                requester=requester,
                retrieved_at=utcnow_naive(),
                query_hash=hashlib.sha256((query or "").encode("utf-8")).hexdigest(),
                retrieval_mode="exact_bible_reference" if parsed else "indexed_bible_topic",
                freshness_requested=False,
                source_ids_json=_dumps(sorted({item["source_id"] for item in recall})),
                chunk_ids_json=_dumps(chunk_ids),
                live_verified_ids_json=_dumps([]),
            )
            db.add(run)
            db.commit()
            for item in recall:
                item.pop("chunk_ids", None)
            return retrieval.QuestRetrievalResult(
                context="\n".join(blocks),
                recall=recall,
                run_id=run.id,
                retrieval_mode=run.retrieval_mode,
                freshness_requested=False,
            )
        finally:
            db.close()

    retrieval.retrieve_quest_evidence = scoped_retrieval
    retrieval._exact_bible_retrieval_installed = True
