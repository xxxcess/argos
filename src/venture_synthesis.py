"""Asynchronous Argos Venture Quest synthesis jobs.

Artifacts are the durable, reviewable record of a Quest. Generated Voyage Memory
is intentionally derived only from the published Artifact's ``Key points`` section.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from core.database import (
    ChatMessage,
    Document,
    QuestArtifactProposal,
    QuestBearing,
    QuestEvidenceChunk,
    QuestMember,
    QuestMemoryEntry,
    QuestMemoryState,
    QuestRetrievalRun,
    QuestSource,
    QuestSynthesisJob,
    Session as DbSession,
    SessionLocal,
    UserNotification,
    utcnow_naive,
)

logger = logging.getLogger(__name__)

JOB_STATUSES = {"queued", "running", "created", "updated", "completed", "no_insight", "failed", "cancelled"}
JOB_TRIGGERS = {"grounded_response", "captain_requested", "artifact_revision"}
TERMINAL_STATUSES = {"created", "updated", "completed", "no_insight", "failed", "cancelled"}
MAX_CONVERSATION_OBSERVATIONS = 3
MAX_CONVERSATION_QUESTIONS = 6
MIN_KEY_POINTS = 3
MAX_KEY_POINTS = 5
MAX_NEXT_BEARINGS = 6
MAX_SOURCE_CHUNKS = 6
_worker_task: asyncio.Task | None = None
_worker_stop: asyncio.Event | None = None


@dataclass
class SynthesisResult:
    created: bool
    updated: bool
    proposal_id: str | None
    evidence_count: int
    reason: str

    def to_dict(self) -> dict:
        return {
            "created": self.created,
            "updated": self.updated,
            "proposal_id": self.proposal_id,
            "evidence_count": self.evidence_count,
            "reason": self.reason,
        }


def _loads(value: str | None, fallback):
    try:
        parsed = json.loads(value or "")
        return parsed if parsed is not None else fallback
    except Exception:
        return fallback


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True)


def _notify(db, user: str, key: str, **fields):
    row = db.query(UserNotification).filter(UserNotification.deterministic_key == key).first()
    if row is None:
        row = UserNotification(
            id=uuid.uuid4().hex,
            user_id=user,
            owner=user,
            deterministic_key=key,
            category=fields.get("category", "inbox"),
            severity=fields.get("severity", "info"),
            state=fields.get("state", "unread"),
            title=fields.get("title", ""),
            message=fields.get("message", ""),
            resource_type=fields.get("resource_type"),
            resource_id=fields.get("resource_id"),
            actions_json=_dumps(fields.get("actions", [])),
        )
        db.add(row)
    else:
        row.category = fields.get("category", row.category)
        row.severity = fields.get("severity", row.severity)
        row.state = fields.get("state", row.state)
        row.title = fields.get("title", row.title)
        row.message = fields.get("message", row.message)
        row.resource_type = fields.get("resource_type", row.resource_type)
        row.resource_id = fields.get("resource_id", row.resource_id)
        row.actions_json = _dumps(fields.get("actions", _loads(row.actions_json, [])))
        row.archived_at = None
        row.updated_at = utcnow_naive()
    return row


def synthesis_job_to_dict(job: QuestSynthesisJob) -> dict:
    return {
        "id": job.id,
        "quest_id": job.quest_id,
        "trigger": job.trigger,
        "status": job.status,
        "created_by": job.created_by,
        "triggering_user_message_id": job.triggering_user_message_id,
        "triggering_assistant_message_id": job.triggering_assistant_message_id,
        "retrieval_run_id": job.retrieval_run_id,
        "artifact_proposal_id": job.artifact_proposal_id,
        "attempt_count": job.attempt_count,
        "requested_at": job.requested_at.isoformat() + "Z" if job.requested_at else None,
        "started_at": job.started_at.isoformat() + "Z" if job.started_at else None,
        "finished_at": job.finished_at.isoformat() + "Z" if job.finished_at else None,
        "next_retry_at": job.next_retry_at.isoformat() + "Z" if job.next_retry_at else None,
        "safe_error_code": job.safe_error_code,
        "safe_error_message": job.safe_error_message,
        "result": _loads(job.result_json, {}),
    }


def enqueue_synthesis_job(
    db,
    *,
    quest_id: str,
    trigger: str,
    created_by: str | None,
    triggering_user_message_id: str | None = None,
    triggering_assistant_message_id: str | None = None,
    retrieval_run_id: str | None = None,
    priority_time=None,
) -> QuestSynthesisJob:
    trigger = trigger if trigger in JOB_TRIGGERS else "grounded_response"
    job = QuestSynthesisJob(
        id=uuid.uuid4().hex,
        quest_id=quest_id,
        trigger=trigger,
        status="queued",
        created_by=created_by,
        triggering_user_message_id=triggering_user_message_id,
        triggering_assistant_message_id=triggering_assistant_message_id,
        retrieval_run_id=retrieval_run_id,
        requested_at=priority_time or utcnow_naive(),
    )
    db.add(job)
    return job


def _normalize_claim_key(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return value[:120] or hashlib.sha256((value or "quest-key-points").encode()).hexdigest()[:24]


def _chunk_text(chunk: QuestEvidenceChunk) -> str:
    artifact = chunk.artifact
    text = (getattr(artifact, "normalized_text_path_or_text", None) or "").strip()
    return text[:1600] if text else ""


def _message_metadata(message: ChatMessage) -> dict[str, Any]:
    data = _loads(getattr(message, "meta_data", None), {})
    return data if isinstance(data, dict) else {}


def _message_author(metadata: dict[str, Any]) -> str:
    for key in ("author", "username", "user", "actor", "sender", "created_by", "owner"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    return ""


def _conversation_speaker(message: ChatMessage, quest: DbSession, memberships: dict[str, str]) -> str:
    if message.role == "assistant":
        return "Argo"
    metadata = _message_metadata(message)
    author = _message_author(metadata)
    if author and author == quest.owner:
        return "Captain"
    if author and memberships.get(author) == "shipmate":
        return f"Shipmate: {author}"
    if metadata.get("is_admin") or str(metadata.get("actor_role") or "").lower() == "admin":
        return "Admin"
    if author:
        return f"Participant: {author}"
    return "Participant"


def _is_substantive_message(message: ChatMessage) -> bool:
    text = re.sub(r"\s+", " ", (message.content or "")).strip()
    if not text:
        return False
    if message.role == "system":
        return False
    lower = text.lower()
    noise_prefixes = (
        "artifact draft created",
        "memory synthesis queued",
        "quest artifact published",
        "artifact draft declined",
    )
    if lower.startswith(noise_prefixes):
        return False
    return len(text) >= 16 or "?" in text


def _message_score(message: ChatMessage) -> int:
    text = (message.content or "").lower()
    score = min(len(text) // 120, 4)
    if "?" in text:
        score += 4
    if any(token in text for token in ("decide", "decision", "should", "must", "need", "risk", "constraint", "because", "next", "question", "blocker", "observ")):
        score += 3
    if message.role == "user":
        score += 1
    return score


def _conversation_evidence(db, quest: DbSession) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Return a compact, chronological conversation window and C# provenance labels."""
    membership_rows = db.query(QuestMember).filter(QuestMember.session_id == quest.id).all()
    memberships = {row.username: row.role for row in membership_rows if row.username}
    rows = db.query(ChatMessage).filter(ChatMessage.session_id == quest.id).order_by(ChatMessage.timestamp.asc(), ChatMessage.id.asc()).all()
    if len(rows) > 48:
        rows = rows[:12] + rows[-36:]
    candidates = [row for row in rows if _is_substantive_message(row)]
    if not candidates:
        return [], {}

    first = candidates[0]
    last = candidates[-1]
    middle_candidates = [row for row in candidates[1:-1] if row.id not in {first.id, last.id}]
    middle = max(middle_candidates, key=_message_score, default=None)
    path_ids = {first.id, last.id}
    if middle:
        path_ids.add(middle.id)

    question_rows = [
        row for row in candidates
        if "?" in (row.content or "")
        or any(marker in (row.content or "").lower() for marker in ("open question", "what should", "should we", "can we", "do we"))
    ]
    selected_ids = set(path_ids)
    for row in question_rows:
        if len(selected_ids) >= MAX_CONVERSATION_OBSERVATIONS + MAX_CONVERSATION_QUESTIONS:
            break
        selected_ids.add(row.id)

    selected = [row for row in candidates if row.id in selected_ids]
    labels: dict[str, dict[str, Any]] = {}
    items: list[dict[str, Any]] = []
    path_sequence = 0
    for row in selected:
        label = f"C{len(items) + 1}"
        is_path = row.id in path_ids
        if is_path:
            path_sequence += 1
        metadata = _message_metadata(row)
        item = {
            "label": label,
            "kind": "conversation",
            "speaker": _conversation_speaker(row, quest, memberships),
            "message_role": row.role,
            "text": re.sub(r"\s+", " ", row.content or "").strip()[:1600],
            "locator": f"Voyage Log · message {len(items) + 1}",
            "message_id": row.id,
            "captured_at": row.timestamp.isoformat() + "Z" if row.timestamp else None,
            "is_conversation_path": is_path,
            "path_sequence": path_sequence if is_path else None,
            "metadata": metadata,
        }
        labels[label] = item
        items.append(item)
    return items, labels


def build_evidence_pack(db, job: QuestSynthesisJob) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    quest = db.query(DbSession).filter(DbSession.id == job.quest_id).first()
    bearing = db.query(QuestBearing).filter(QuestBearing.session_id == job.quest_id).first()
    if not quest or not bearing:
        raise ValueError("quest_not_found")

    conversation_items, labels = _conversation_evidence(db, quest)
    run = db.query(QuestRetrievalRun).filter(
        QuestRetrievalRun.id == job.retrieval_run_id,
        QuestRetrievalRun.quest_id == job.quest_id,
    ).first() if job.retrieval_run_id else None
    chunk_ids = _loads(run.chunk_ids_json, []) if run else []
    chunks: list[QuestEvidenceChunk] = []
    if chunk_ids:
        rows = db.query(QuestEvidenceChunk).filter(
            QuestEvidenceChunk.quest_id == job.quest_id,
            QuestEvidenceChunk.id.in_(chunk_ids),
            QuestEvidenceChunk.is_current == True,  # noqa: E712
        ).all()
        by_id = {row.id: row for row in rows}
        chunks = [by_id[chunk_id] for chunk_id in chunk_ids if chunk_id in by_id]
    if not chunks and job.trigger == "captain_requested":
        chunks = db.query(QuestEvidenceChunk).filter(
            QuestEvidenceChunk.quest_id == job.quest_id,
            QuestEvidenceChunk.is_current == True,  # noqa: E712
        ).order_by(QuestEvidenceChunk.created_at.desc()).limit(MAX_SOURCE_CHUNKS).all()

    source_items = []
    for chunk in chunks[:MAX_SOURCE_CHUNKS]:
        source = db.query(QuestSource).filter(
            QuestSource.id == chunk.source_id,
            QuestSource.session_id == job.quest_id,
        ).first()
        text = _chunk_text(chunk)
        if not source or not text:
            continue
        label = f"S{len(source_items) + 1}"
        item = {
            "label": label,
            "kind": "source",
            "source_name": source.display_name,
            "source_type": source.source_type,
            "locator": chunk.locator,
            "text": text,
            "chunk_id": chunk.id,
            "source_version_id": chunk.source_version_id,
            "captured_at": chunk.version.captured_at.isoformat() + "Z" if getattr(chunk, "version", None) and chunk.version.captured_at else None,
        }
        labels[label] = item
        source_items.append(item)

    pack = {
        "quest_title": quest.name,
        "current_bearing": {
            "title": bearing.title,
            "exploration_goal": bearing.exploration_goal,
            "current_summary": bearing.current_summary,
            "next_bearing": bearing.next_bearing,
        },
        "conversation_items": [
            {key: value for key, value in item.items() if key not in {"message_id", "metadata"}}
            for item in conversation_items
        ],
        "source_chunks": [
            {key: value for key, value in item.items() if key not in {"chunk_id", "source_version_id"}}
            for item in source_items
        ],
    }
    return pack, labels


SYNTHESIS_SYSTEM_PROMPT = """\
Create a Captain-reviewable Quest Artifact from the supplied conversation and Quest evidence.
Return strict JSON only. Return {"should_create": false} when the material cannot support a durable Artifact.
Use only supplied material for factual claims. Do not invent speakers, decisions, questions, dates, or evidence.

The JSON must contain:
- title: a concise, specific title summarizing the key points. Never use generic titles such as Insights, Quest Insight, Summary, or Artifact.
- claim_key
- conversation_observations: 1 to 3 chronological observations explaining how the Quest reached the current point. Each must include speaker, kind, text, and at least one C# citation. Cite only conversation items marked is_conversation_path=true.
- questions_raised: every actionable question visible in the supplied conversation window, each with text and one or more C# citations.
- key_points: 3 to 5 durable points. Each must include title, content, category, confidence, and one or more C# or S# citations.
- interpretation and recommended_next_bearing.

Every observation, question, and key point needs valid supplied citations. Distinguish observation, inference, decision, request, constraint, question, and risk. Do not expose internal IDs or indexing telemetry.
"""


def _fallback_title(text: str) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip(" .:-")
    sentence = re.split(r"(?<=[.!?])\s", clean, maxsplit=1)[0]
    return sentence[:120].rstrip(" .:-") or "Evidence-backed Quest key points"


def _fallback_synthesis(pack: dict[str, Any]) -> dict[str, Any]:
    conversations = [item for item in (pack.get("conversation_items") or []) if item.get("is_conversation_path")]
    evidence = list(pack.get("source_chunks") or []) + list(pack.get("conversation_items") or [])
    if not conversations or len(evidence) < MIN_KEY_POINTS:
        return {"should_create": False, "reason": "insufficient_grounded_conversation"}
    observations = [
        {
            "speaker": item.get("speaker") or "Participant",
            "kind": "question" if "?" in item.get("text", "") else "observation",
            "text": item.get("text", "")[:500],
            "citations": [item["label"]],
        }
        for item in conversations[:MAX_CONVERSATION_OBSERVATIONS]
    ]
    questions = [
        {"text": item.get("text", "")[:500], "citations": [item["label"]]}
        for item in (pack.get("conversation_items") or [])
        if "?" in item.get("text", "")
    ][:MAX_CONVERSATION_QUESTIONS]
    key_points = []
    for item in evidence:
        text = re.sub(r"\s+", " ", item.get("text") or "").strip()
        if len(text) < 24:
            continue
        key_points.append({
            "title": _fallback_title(text)[:96],
            "content": text[:500],
            "category": "open_question" if "?" in text else "finding",
            "confidence": "medium" if item.get("label", "").startswith("S") else "low",
            "citations": [item["label"]],
        })
        if len(key_points) == MIN_KEY_POINTS:
            break
    if len(key_points) < MIN_KEY_POINTS:
        return {"should_create": False, "reason": "insufficient_grounded_conversation"}
    return {
        "should_create": True,
        "title": key_points[0]["title"],
        "claim_key": _normalize_claim_key(key_points[0]["title"]),
        "conversation_observations": observations,
        "questions_raised": questions,
        "key_points": key_points,
        "interpretation": "These points are limited to the cited Quest discussion and source evidence.",
        "recommended_next_bearing": ["Review the cited Artifact draft before publishing it to Voyage Memory."],
    }


async def _call_synthesis_model(pack: dict[str, Any], labels: dict[str, Any]) -> dict[str, Any]:
    if not pack.get("conversation_items") and not pack.get("source_chunks"):
        return {"should_create": False, "reason": "no_grounded_material"}
    try:
        from src.llm_core import llm_call_async
        from src.task_endpoint import resolve_task_endpoint

        endpoint, model, headers = resolve_task_endpoint("", "", {}, owner=None)
        raw = await llm_call_async(
            endpoint,
            model,
            [
                {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps({"evidence_pack": pack}, ensure_ascii=False)},
            ],
            headers=headers,
            temperature=0.1,
            max_tokens=2200,
            tools=None,
        )
        return json.loads(raw)
    except Exception:
        logger.info("Quest synthesis model unavailable; using conservative local fallback", exc_info=True)
        return _fallback_synthesis(pack)


def _citation_list(values: Any, valid: set[str]) -> list[str]:
    citations = []
    for value in values or []:
        citation = str(value).strip()
        if citation not in valid:
            raise ValueError("unsupported_citation")
        if citation not in citations:
            citations.append(citation)
    if not citations:
        raise ValueError("missing_citation")
    return citations


def _is_generic_title(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    return normalized in {"", "insight", "insights", "quest insight", "summary", "artifact", "quest artifact", "key points"}


def _first_cited_speaker(citations: list[str], labels: dict[str, dict[str, Any]]) -> str:
    for citation in citations:
        speaker = labels.get(citation, {}).get("speaker")
        if speaker:
            return str(speaker)
    return "Participant"


def validate_synthesis_json(data: dict[str, Any], labels: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("invalid_json")
    if data.get("should_create") is False:
        return {"should_create": False, "reason": str(data.get("reason") or "no_durable_insight")}

    valid = {label for label in labels if label.startswith(("C", "S"))}
    if not valid:
        return {"should_create": False, "reason": "no_grounded_material"}

    observations = []
    raw_observations = data.get("conversation_observations") or data.get("observations") or []
    for raw in raw_observations[:MAX_CONVERSATION_OBSERVATIONS]:
        if not isinstance(raw, dict):
            continue
        citations = _citation_list(raw.get("citations"), valid)
        if not any(citation.startswith("C") for citation in citations):
            raise ValueError("conversation_observation_requires_conversation_citation")
        text = re.sub(r"\s+", " ", str(raw.get("text") or "")).strip()[:800]
        if not text:
            continue
        kind = str(raw.get("kind") or "observation").strip().lower()
        if kind not in {"request", "observation", "decision", "constraint", "question", "inference", "risk"}:
            kind = "observation"
        observations.append({
            "speaker": str(raw.get("speaker") or _first_cited_speaker(citations, labels))[:120],
            "kind": kind,
            "text": text,
            "citations": citations,
        })
    if not observations:
        return {"should_create": False, "reason": "insufficient_grounded_conversation"}

    questions = []
    for raw in (data.get("questions_raised") or [])[:MAX_CONVERSATION_QUESTIONS]:
        if not isinstance(raw, dict):
            continue
        text = re.sub(r"\s+", " ", str(raw.get("text") or "")).strip()[:500]
        if not text:
            continue
        citations = _citation_list(raw.get("citations"), valid)
        if not any(citation.startswith("C") for citation in citations):
            raise ValueError("question_requires_conversation_citation")
        questions.append({"text": text, "citations": citations})

    key_points = []
    seen = set()
    for raw in (data.get("key_points") or [])[:MAX_KEY_POINTS]:
        if not isinstance(raw, dict):
            continue
        title = re.sub(r"\s+", " ", str(raw.get("title") or "")).strip()[:120]
        content = re.sub(r"\s+", " ", str(raw.get("content") or "")).strip()[:700]
        citations = _citation_list(raw.get("citations"), valid)
        if not title or not content:
            continue
        signature = re.sub(r"\W+", " ", f"{title} {content}").strip().lower()
        if signature in seen:
            continue
        seen.add(signature)
        category = str(raw.get("category") or "finding").strip().lower()
        if category not in {"finding", "decision", "risk", "open_question", "constraint", "next_step", "contradiction"}:
            category = "finding"
        confidence = str(raw.get("confidence") or "medium").strip().lower()
        if confidence not in {"low", "medium", "high"}:
            confidence = "medium"
        key_points.append({
            "title": title,
            "content": content,
            "category": category,
            "confidence": confidence,
            "citations": citations,
        })
    if len(key_points) < MIN_KEY_POINTS:
        return {"should_create": False, "reason": "insufficient_grounded_key_points"}

    title = re.sub(r"\s+", " ", str(data.get("title") or "")).strip()[:140]
    if _is_generic_title(title):
        title = key_points[0]["title"][:140]
    key_finding = re.sub(r"\s+", " ", str(data.get("key_finding") or key_points[0]["content"]).strip())[:1200]
    return {
        "should_create": True,
        "title": title,
        "claim_key": _normalize_claim_key(str(data.get("claim_key") or title)),
        "conversation_observations": observations,
        "questions_raised": questions,
        "key_points": key_points,
        "key_finding": key_finding,
        "interpretation": re.sub(r"\s+", " ", str(data.get("interpretation") or "")).strip()[:1600],
        "recommended_next_bearing": [
            re.sub(r"\s+", " ", str(value)).strip()[:240]
            for value in (data.get("recommended_next_bearing") or [])[:MAX_NEXT_BEARINGS]
            if str(value).strip()
        ],
    }


def _specific_evidence_label(label: str, item: dict[str, Any]) -> str:
    if label.startswith("C"):
        return str(item.get("speaker") or "Quest discussion")
    if label.startswith("S"):
        parts = [item.get("source_name") or "Quest source", item.get("locator")]
        return " - ".join(str(part) for part in parts if part)
    return label


def _cited_labels(synth: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for item in synth.get("conversation_observations", []):
        for citation in item.get("citations", []):
            if citation not in labels:
                labels.append(citation)
    for item in synth.get("questions_raised", []):
        for citation in item.get("citations", []):
            if citation not in labels:
                labels.append(citation)
    for item in synth.get("key_points", []):
        for citation in item.get("citations", []):
            if citation not in labels:
                labels.append(citation)
    return labels


def render_artifact_markdown(synth: dict[str, Any], labels: dict[str, dict[str, Any]]) -> str:
    cited = [label for label in _cited_labels(synth) if label in labels]
    evidence_times = [labels[label].get("captured_at") for label in cited if labels[label].get("captured_at")]
    evidence_window = f"{min(evidence_times)}-{max(evidence_times)}" if evidence_times else "unspecified"
    lines = [
        f"# {synth['title']}",
        "",
        f"**Quest:** {synth.get('quest_title') or 'Quest'}  ",
        f"**Current Bearing:** {synth.get('current_bearing') or 'See Voyage Log'}  ",
        "**Artifact status:** draft  ",
        f"**Evidence window:** {evidence_window}",
        "",
        "## Conversation path",
    ]
    for index, observation in enumerate(synth.get("conversation_observations", []), 1):
        citations = " ".join(f"[{citation}]" for citation in observation.get("citations", []) if citation in labels)
        lines.extend([
            "",
            f"{index}. **{observation.get('speaker') or 'Participant'} — {observation.get('kind', 'observation').title()}**: {observation['text']} {citations}".rstrip(),
        ])
    questions = synth.get("questions_raised") or []
    if questions:
        lines.extend(["", "## Questions raised"])
        for question in questions:
            citations = " ".join(f"[{citation}]" for citation in question.get("citations", []) if citation in labels)
            lines.append(f"- {question['text']} {citations}".rstrip())
    lines.extend(["", "## Key points"])
    for index, point in enumerate(synth.get("key_points", []), 1):
        citations = " ".join(f"[{citation}]" for citation in point.get("citations", []) if citation in labels)
        lines.extend(["", f"{index}. **{point['title']}**", f"{point['content']} {citations}".rstrip()])
    if synth.get("interpretation"):
        lines.extend(["", "## Why this matters", synth["interpretation"]])
    next_steps = synth.get("recommended_next_bearing") or []
    if next_steps:
        lines.extend(["", "## Recommended next bearing"])
        lines.extend(f"{index}. {step}" for index, step in enumerate(next_steps, 1))
    lines.extend(["", "## Evidence index", "", "| ID | Source | Locator | Supports |", "|---|---|---|---|"])
    for label in cited:
        item = labels[label]
        supports = []
        for observation in synth.get("conversation_observations", []):
            if label in observation.get("citations", []):
                supports.append(observation.get("text", "")[:100])
        for question in synth.get("questions_raised", []):
            if label in question.get("citations", []):
                supports.append(question.get("text", "")[:100])
        for point in synth.get("key_points", []):
            if label in point.get("citations", []):
                supports.append(point.get("content", "")[:100])
        source = _specific_evidence_label(label, item).replace("|", "\\|")
        locator = str(item.get("locator") or "").replace("|", "\\|")
        support_text = "; ".join(supports).replace("|", "\\|")
        lines.append(f"| {label} | {source} | {locator} | {support_text} |")
    return "\n".join(lines).rstrip() + "\n"


def _fingerprint(synth: dict[str, Any], labels: dict[str, dict[str, Any]]) -> str:
    evidence = []
    for label in _cited_labels(synth):
        item = labels.get(label, {})
        evidence.append({
            "label": label,
            "chunk_id": item.get("chunk_id"),
            "message_id": item.get("message_id"),
            "text": item.get("text", "")[:160],
        })
    return hashlib.sha256(_dumps({"claim_key": synth["claim_key"], "key_points": synth["key_points"], "evidence": evidence}).encode()).hexdigest()


def persist_synthesis_result(db, job: QuestSynthesisJob, synth: dict[str, Any], labels: dict[str, dict[str, Any]]) -> SynthesisResult:
    if not synth.get("should_create"):
        job.status = "no_insight"
        job.finished_at = utcnow_naive()
        job.result_json = _dumps({"should_create": False, "reason": synth.get("reason", "no_durable_insight")})
        return SynthesisResult(False, False, None, 0, str(synth.get("reason") or "no_insight"))
    quest = db.query(DbSession).filter(DbSession.id == job.quest_id).first()
    if not quest:
        raise ValueError("quest_not_found")
    bearing = db.query(QuestBearing).filter(QuestBearing.session_id == job.quest_id).first()
    synth = {
        **synth,
        "quest_title": quest.name,
        "current_bearing": (bearing.current_summary or bearing.next_bearing or bearing.exploration_goal or bearing.title) if bearing else "",
    }
    fingerprint = _fingerprint(synth, labels)
    source_versions = sorted({item["source_version_id"] for item in labels.values() if item.get("source_version_id")})
    chunk_ids = sorted({item["chunk_id"] for item in labels.values() if item.get("chunk_id")})
    evidence_refs = [label for label in _cited_labels(synth) if label.startswith(("C", "S"))]
    existing = db.query(QuestArtifactProposal).filter(
        QuestArtifactProposal.session_id == job.quest_id,
        QuestArtifactProposal.claim_key == synth["claim_key"],
        QuestArtifactProposal.artifact_type == "insight",
        QuestArtifactProposal.visibility == "captain_private",
        QuestArtifactProposal.status == "pending_review",
    ).first()
    same_evidence = db.query(QuestArtifactProposal).filter(
        QuestArtifactProposal.session_id == job.quest_id,
        QuestArtifactProposal.claim_key == synth["claim_key"],
        QuestArtifactProposal.artifact_type == "insight",
        QuestArtifactProposal.visibility == "captain_private",
        QuestArtifactProposal.evidence_fingerprint == fingerprint,
    ).first()
    if same_evidence and same_evidence.status != "pending_review":
        job.status = "no_insight"
        job.finished_at = utcnow_naive()
        job.result_json = _dumps({"should_create": False, "reason": "duplicate_claim_without_new_evidence"})
        return SynthesisResult(False, False, None, len(chunk_ids), "duplicate_claim_without_new_evidence")

    content = render_artifact_markdown(synth, labels)
    if existing:
        existing.title = synth["title"]
        existing.summary = synth["key_finding"]
        existing.evidence_refs_json = _dumps(evidence_refs)
        existing.evidence_fingerprint = fingerprint
        existing.source_version_refs_json = _dumps(source_versions)
        existing.evidence_chunk_refs_json = _dumps(chunk_ids)
        existing.retrieval_run_ids_json = _dumps([job.retrieval_run_id] if job.retrieval_run_id else [])
        existing.synthesis_json = _dumps(synth)
        existing.updated_at = utcnow_naive()
        if existing.document:
            existing.document.title = synth["title"]
            existing.document.current_content = content
            existing.document.updated_at = utcnow_naive()
        job.artifact_proposal_id = existing.id
        job.status = "updated"
        job.finished_at = utcnow_naive()
        job.result_json = _dumps(synth)
        return SynthesisResult(False, True, existing.id, len(chunk_ids), "updated_existing_draft")

    document = Document(
        id=uuid.uuid4().hex,
        session_id=None,
        title=synth["title"],
        language="markdown",
        current_content=content,
        owner=quest.owner,
        is_active=False,
    )
    db.add(document)
    proposal = QuestArtifactProposal(
        id=uuid.uuid4().hex,
        session_id=job.quest_id,
        document_id=document.id,
        captain_username=quest.owner,
        artifact_type="insight",
        title=synth["title"],
        summary=synth["key_finding"],
        evidence_refs_json=_dumps(evidence_refs),
        evidence_fingerprint=fingerprint,
        source_version_refs_json=_dumps(source_versions),
        claim_key=synth["claim_key"],
        evidence_chunk_refs_json=_dumps(chunk_ids),
        retrieval_run_ids_json=_dumps([job.retrieval_run_id] if job.retrieval_run_id else []),
        synthesis_json=_dumps(synth),
        revision_number=1,
        visibility="captain_private",
    )
    db.add(proposal)
    db.flush()
    _notify(
        db,
        quest.owner,
        f"artifact-proposal:{proposal.id}",
        category="inbox",
        state="action_required",
        title="New Artifact Draft ready",
        message=f"Review '{proposal.title}' before publishing it to the Quest.",
        resource_type="quest_artifact_proposal",
        resource_id=proposal.id,
        actions=[
            {"id": "review", "label": "Review Draft", "style": "secondary"},
            {"id": "publish", "label": "Publish to Quest", "style": "primary"},
            {"id": "decline", "label": "Decline", "style": "secondary"},
        ],
    )
    job.artifact_proposal_id = proposal.id
    job.status = "created"
    job.finished_at = utcnow_naive()
    job.result_json = _dumps(synth)
    return SynthesisResult(True, False, proposal.id, len(chunk_ids), "draft_created")


async def process_synthesis_job(job_id: str) -> None:
    db = SessionLocal()
    try:
        job = db.query(QuestSynthesisJob).filter(QuestSynthesisJob.id == job_id).first()
        if not job or job.status not in {"queued", "running"}:
            return
        job.status = "running"
        job.started_at = job.started_at or utcnow_naive()
        job.attempt_count = (job.attempt_count or 0) + 1
        db.commit()
        if job.trigger == "artifact_revision":
            result = _process_artifact_memory_synthesis(db, job)
            db.commit()
            logger.debug("[venture-memory-synthesis] %s", result)
            return
        pack, labels = build_evidence_pack(db, job)
        if not any(label.startswith(("C", "S")) for label in labels):
            job.status = "no_insight"
            job.safe_error_code = None
            job.safe_error_message = None
            job.result_json = _dumps({"should_create": False, "reason": "no_grounded_material"})
            job.finished_at = utcnow_naive()
            db.commit()
            return
        raw = await _call_synthesis_model(pack, labels)
        synth = validate_synthesis_json(raw, labels)
        result = persist_synthesis_result(db, job, synth, labels)
        db.commit()
        logger.debug("[venture-synthesis] %s", result.to_dict())
    except Exception as exc:
        db.rollback()
        job = db.query(QuestSynthesisJob).filter(QuestSynthesisJob.id == job_id).first()
        if job:
            code = re.sub(r"[^a-zA-Z0-9_ -]", "_", (str(exc) or type(exc).__name__).split(":", 1)[0])[:80].lower()
            transient = code not in {
                "unsupported_citation",
                "missing_citation",
                "invalid_json",
                "quest_not_found",
                "conversation_observation_requires_conversation_citation",
                "question_requires_conversation_citation",
            }
            if transient and job.attempt_count < 3:
                job.status = "queued"
                job.next_retry_at = utcnow_naive() + timedelta(seconds=min(60 * (2 ** max(job.attempt_count - 1, 0)), 900))
            else:
                job.status = "failed"
                job.finished_at = utcnow_naive()
            job.safe_error_code = code
            job.safe_error_message = "Quest synthesis failed safely."
            db.commit()
        logger.info("Quest synthesis job %s failed: %s", job_id, exc)
    finally:
        db.close()


def _evidence_index(content: str) -> dict[str, dict[str, str]]:
    evidence: dict[str, dict[str, str]] = {}
    in_index = False
    for raw in content.splitlines():
        line = raw.strip()
        if line.lower() == "## evidence index":
            in_index = True
            continue
        if in_index and line.startswith("## "):
            break
        if not in_index or not line.startswith("|") or line.startswith("|---") or " ID " in line:
            continue
        cells = [cell.strip().replace("\\|", "|") for cell in line.strip("|").split("|")]
        if len(cells) >= 4 and re.fullmatch(r"[CSA]\d+", cells[0] or ""):
            evidence[cells[0]] = {"label": cells[1], "locator": cells[2], "supports": cells[3]}
    return evidence


def _key_point_category(title: str, content: str) -> str:
    lower = f"{title} {content}".lower()
    if "?" in content or "question" in lower:
        return "open_question"
    if "risk" in lower:
        return "risk"
    if "constraint" in lower or "must" in lower:
        return "constraint"
    if lower.startswith(("decide", "decision")) or " decided " in f" {lower} ":
        return "decision"
    if "next" in lower or "should" in lower:
        return "next_step"
    if "contradict" in lower or "conflict" in lower:
        return "contradiction"
    return "finding"


def _is_recall_worthy_artifact_claim(claim: str) -> bool:
    text = re.sub(r"\s+", " ", claim or "").strip()
    lower = text.lower()
    if len(text) < 32:
        return False
    boilerplate = (
        "these points are limited",
        "review the cited artifact",
        "artifact status",
        "evidence window",
        "current bearing",
        "recommended next bearing",
    )
    if any(marker in lower for marker in boilerplate):
        return False
    return bool(re.search(r"[a-zA-Z]", text))


def _extract_artifact_key_point_candidates(prop: QuestArtifactProposal) -> list[dict[str, Any]]:
    document = getattr(prop, "document", None)
    content = (getattr(document, "current_content", None) or "").strip()
    if not content:
        return []
    evidence_index = _evidence_index(content)
    in_key_points = False
    current: dict[str, Any] | None = None
    blocks: list[dict[str, Any]] = []
    for raw in content.splitlines():
        line = raw.strip()
        if line.lower() == "## key points":
            in_key_points = True
            continue
        if in_key_points and line.startswith("## "):
            break
        if not in_key_points or not line:
            continue
        match = re.match(r"^\d+\.\s+\*\*(.+?)\*\*\s*$", line)
        if match:
            if current:
                blocks.append(current)
            current = {"title": match.group(1).strip(), "lines": []}
            continue
        if current:
            current["lines"].append(line)
    if current:
        blocks.append(current)

    candidates = []
    for block in blocks[:MAX_KEY_POINTS]:
        raw_content = " ".join(block.get("lines") or []).strip()
        citations = []
        for citation in re.findall(r"\[(C\d+|S\d+|A\d+)\]", raw_content):
            if citation not in citations:
                citations.append(citation)
        claim = re.sub(r"\s*\[(?:C\d+|S\d+|A\d+)\]", "", raw_content).strip(" -")
        if not citations or not _is_recall_worthy_artifact_claim(claim):
            continue
        evidence = []
        for citation in citations:
            info = evidence_index.get(citation, {})
            evidence.append({
                "id": citation,
                "label": info.get("label") or citation,
                "locator": info.get("locator") or "",
                "supports": info.get("supports") or claim,
            })
        candidates.append({
            "title": str(block.get("title") or claim)[:120].rstrip("."),
            "content": claim[:500],
            "category": _key_point_category(str(block.get("title") or ""), claim),
            "confidence": "high" if any(citation.startswith("S") for citation in citations) else "medium",
            "citations": citations,
            "evidence": evidence,
        })
    return candidates


def _artifact_memory_fingerprint(candidate: dict[str, Any], revision_number: int) -> str:
    payload = {
        "title": re.sub(r"\W+", " ", candidate.get("title", "")).strip().lower(),
        "content": re.sub(r"\W+", " ", candidate.get("content", "")).strip().lower(),
        "citations": sorted(candidate.get("citations") or []),
        "revision": revision_number,
    }
    return hashlib.sha256(_dumps(payload).encode()).hexdigest()


def _process_artifact_memory_synthesis(db, job: QuestSynthesisJob) -> dict[str, Any]:
    proposal = db.query(QuestArtifactProposal).filter(
        QuestArtifactProposal.id == job.artifact_proposal_id,
        QuestArtifactProposal.session_id == job.quest_id,
    ).first()
    if not proposal or proposal.status != "published":
        job.status = "failed"
        job.finished_at = utcnow_naive()
        job.safe_error_code = "artifact_not_published"
        job.safe_error_message = "Memory synthesis could not find the published Artifact."
        return {"changed_memory_ids": [], "reason": "artifact_not_published"}

    candidates = _extract_artifact_key_point_candidates(proposal)
    changed = []
    source_versions = _loads(proposal.source_version_refs_json, [])
    evidence_chunks = _loads(proposal.evidence_chunk_refs_json, [])
    for candidate in candidates[:MAX_KEY_POINTS]:
        title = str(candidate.get("title") or proposal.title).strip()[:120]
        content = str(candidate.get("content") or "").strip()[:500]
        if not title or not content:
            continue
        fingerprint = _artifact_memory_fingerprint(candidate, proposal.revision_number or 1)
        existing = db.query(QuestMemoryEntry).filter(
            QuestMemoryEntry.session_id == job.quest_id,
            QuestMemoryEntry.artifact_id == proposal.id,
            QuestMemoryEntry.artifact_revision_number == proposal.revision_number,
            QuestMemoryEntry.title == title,
            QuestMemoryEntry.content == content,
        ).first()
        if existing:
            continue
        memory = QuestMemoryEntry(
            id=uuid.uuid4().hex,
            session_id=job.quest_id,
            category=candidate.get("category") if candidate.get("category") in {"finding", "decision", "risk", "open_question", "constraint", "next_step", "contradiction"} else "finding",
            visibility="quest_shared",
            state="confirmed",
            title=title,
            content=content,
            confidence=candidate.get("confidence") if candidate.get("confidence") in {"low", "medium", "high"} else "medium",
            provenance_json=_dumps({
                "artifact_proposal_id": proposal.id,
                "document_id": proposal.document_id,
                "artifact_key_point_fingerprint": fingerprint,
                "citations": candidate.get("citations") or [],
                "evidence": candidate.get("evidence") or [],
                "trigger_job_id": job.id,
            }),
            source_version_refs_json=_dumps(source_versions),
            evidence_chunk_refs_json=_dumps(evidence_chunks),
            artifact_id=proposal.id,
            artifact_revision_number=proposal.revision_number,
            claim_key=proposal.claim_key,
            created_by="argo",
        )
        db.add(memory)
        changed.append(memory.id)
    job.status = "completed"
    job.finished_at = utcnow_naive()
    summary = (
        f"{len(changed)} durable memory entr{'y' if len(changed) == 1 else 'ies'} created from published Artifact key points."
        if changed else "No new durable memory; the published Artifact key points already exist in Voyage Memory."
    )
    job.result_json = _dumps({"changed_memory_ids": changed, "safe_summary": summary, "reason": "completed" if changed else "no_op"})
    db.add(ChatMessage(
        id=uuid.uuid4().hex,
        session_id=job.quest_id,
        role="system",
        content=summary,
        meta_data=_dumps({
            "event_type": "memory_synthesis",
            "presentation": "background_task",
            "event_id": f"memory-synthesis:artifact:{proposal.document_id}",
            "task_id": job.id,
            "status": "completed",
            "title": "Memory synthesis",
            "subject": proposal.title,
            "completed_at": job.finished_at.isoformat() + "Z" if job.finished_at else None,
            "result": {"changed_memory_count": len(changed), "summary": summary},
            "artifact_id": proposal.document_id,
            "quest_id": job.quest_id,
        }),
    ))
    return {"changed_memory_ids": changed, "safe_summary": summary}


def _claim_next_job() -> str | None:
    db = SessionLocal()
    try:
        now = utcnow_naive()
        stale_before = now - timedelta(minutes=30)
        db.query(QuestSynthesisJob).filter(
            QuestSynthesisJob.status == "running",
            QuestSynthesisJob.started_at < stale_before,
        ).update({"status": "queued", "next_retry_at": now}, synchronize_session=False)
        job = db.query(QuestSynthesisJob).filter(
            QuestSynthesisJob.status == "queued",
        ).filter(
            (QuestSynthesisJob.next_retry_at == None) | (QuestSynthesisJob.next_retry_at <= now),  # noqa: E711
        ).order_by(QuestSynthesisJob.requested_at.asc()).first()
        if not job:
            db.commit()
            return None
        job.status = "running"
        job.started_at = now
        db.commit()
        return job.id
    finally:
        db.close()


async def _worker_loop(concurrency: int = 1) -> None:
    global _worker_stop
    semaphore = asyncio.Semaphore(max(1, concurrency))
    active: set[asyncio.Task] = set()
    _worker_stop = asyncio.Event()
    while not _worker_stop.is_set():
        job_id = _claim_next_job()
        if not job_id:
            await asyncio.sleep(2.0)
            continue
        await semaphore.acquire()
        task = asyncio.create_task(process_synthesis_job(job_id))
        active.add(task)
        task.add_done_callback(lambda completed: (active.discard(completed), semaphore.release()))
    if active:
        await asyncio.gather(*active, return_exceptions=True)


def start_quest_synthesis_worker(app=None, *, concurrency: int = 1) -> None:
    global _worker_task
    if _worker_task and not _worker_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _worker_task = loop.create_task(_worker_loop(concurrency=concurrency))
    if app is not None:
        try:
            app.state.quest_synthesis_worker = _worker_task
        except Exception:
            pass


async def stop_quest_synthesis_worker() -> None:
    global _worker_stop, _worker_task
    if _worker_stop:
        _worker_stop.set()
    if _worker_task:
        await asyncio.gather(_worker_task, return_exceptions=True)
    _worker_task = None
    _worker_stop = None


def run_argo_synthesis(db, session_id: str, captain_username: str) -> SynthesisResult:
    """Compatibility wrapper: queue synthesis; never synthesize inline."""
    user_message = db.query(ChatMessage).filter(
        ChatMessage.session_id == session_id,
        ChatMessage.role == "user",
    ).order_by(ChatMessage.timestamp.desc()).first()
    assistant_message = db.query(ChatMessage).filter(
        ChatMessage.session_id == session_id,
        ChatMessage.role == "assistant",
    ).order_by(ChatMessage.timestamp.desc()).first()
    job = enqueue_synthesis_job(
        db,
        quest_id=session_id,
        trigger="captain_requested",
        created_by=captain_username,
        triggering_user_message_id=user_message.id if user_message else None,
        triggering_assistant_message_id=assistant_message.id if assistant_message else None,
    )
    return SynthesisResult(False, False, job.id, 0, "queued")


def set_synthesis_paused(db, session_id: str, paused: bool) -> dict:
    state = db.query(QuestMemoryState).filter(QuestMemoryState.session_id == session_id).first()
    if state is None:
        state = QuestMemoryState(session_id=session_id)
        db.add(state)
    payload = _loads(state.current_bearing_json, {})
    payload["synthesis_paused"] = bool(paused)
    state.current_bearing_json = _dumps(payload)
    state.updated_at = utcnow_naive()
    return payload
