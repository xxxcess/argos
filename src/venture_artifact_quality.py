"""Quality contract for Venture Artifacts and Artifact-derived Voyage Memory.

This module is installed by the Venture route facade during application setup. It
keeps the Artifact lifecycle in ``src.venture_synthesis`` while replacing the
quality-sensitive hooks with a stricter contract:

* Artifact and key-point titles must be semantic summaries, never list labels.
* Key points contain a concise, plain-language ``Revelation`` rather than a
  copied source passage.
* Published Artifact memory is extracted only from explicit Revelation lines.
* Historical Artifact-derived evidence dumps are hidden from Voyage Memory.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from difflib import SequenceMatcher
from typing import Any

GENERIC_TITLES = {
    "",
    "artifact",
    "artifact insight",
    "insight",
    "insights",
    "key point",
    "key points",
    "quest artifact",
    "quest insight",
    "summary",
    "untitled",
}
ALLOWED_CATEGORIES = {
    "finding",
    "decision",
    "risk",
    "open_question",
    "constraint",
    "next_step",
    "contradiction",
}
ALLOWED_CONFIDENCE = {"low", "medium", "high"}


def _clean(value: Any, limit: int | None = None) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit].rstrip() if limit else text


def _words(value: str) -> list[str]:
    return re.findall(r"[A-Za-z][A-Za-z'-]*", value or "")


def _normal(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def is_semantic_title(value: Any) -> bool:
    """A title is a human summary, not a number, locator, citation, or quote."""
    title = _clean(value, 140)
    normalized = _normal(title)
    if normalized in GENERIC_TITLES:
        return False
    if not re.search(r"[A-Za-z]", title):
        return False
    if re.fullmatch(r"\s*\d+[.)-]?\s*", title):
        return False
    if re.match(r"^\s*(?:[CSA]\d+|\d+)\s*[.)-]", title, flags=re.I):
        return False
    if re.search(r"\[(?:C|S|A)\d+\]", title):
        return False
    if re.match(r"^(?:matthew|mark|luke|john|acts|romans|genesis|exodus)\s+\d+[:.]\d+", title, flags=re.I):
        return False
    words = _words(title)
    if len(words) < 3 or len(title) < 14:
        return False
    if len(title) > 120:
        return False
    return True


def _citation_list(values: Any, valid: set[str]) -> list[str]:
    citations: list[str] = []
    for value in values or []:
        citation = str(value).strip()
        if citation not in valid:
            raise ValueError("unsupported_citation")
        if citation not in citations:
            citations.append(citation)
    if not citations:
        raise ValueError("missing_citation")
    return citations


def _numbered_passage(text: str) -> bool:
    return len(re.findall(r"(?:^|\s)\d+\.\s+[A-Z]", text or "")) >= 2


def _looks_like_evidence_echo(text: str, citations: list[str], labels: dict[str, dict[str, Any]]) -> bool:
    """Reject verse/transcript copies so Memory remains a retrieval shortcut."""
    clean = _clean(text, 800)
    if len(clean) > 360 or _numbered_passage(clean):
        return True
    if clean.count('"') >= 2 or clean.count('“') >= 2:
        return True
    normalized = _normal(clean)
    if len(normalized) < 24:
        return True
    for citation in citations:
        source = _clean(labels.get(citation, {}).get("text"), 1800)
        source_normal = _normal(source)
        if len(source_normal) < 50:
            continue
        if len(normalized) >= 60 and normalized in source_normal:
            return True
        ratio = SequenceMatcher(None, normalized, source_normal[: max(len(normalized) * 2, 240)]).ratio()
        if len(normalized) >= 80 and ratio >= 0.82:
            return True
    return False


def is_revelation(value: Any, citations: list[str] | None = None, labels: dict[str, dict[str, Any]] | None = None) -> bool:
    text = _clean(value, 420)
    if len(text) < 24 or len(_words(text)) < 6:
        return False
    if _numbered_passage(text):
        return False
    if labels is not None and _looks_like_evidence_echo(text, citations or [], labels):
        return False
    return True


def _speaker_for(citations: list[str], labels: dict[str, dict[str, Any]]) -> str:
    for citation in citations:
        speaker = labels.get(citation, {}).get("speaker")
        if speaker:
            return _clean(speaker, 120)
    return "Participant"


def _key_point_category(title: str, revelation: str, declared: Any) -> str:
    category = _clean(declared).lower()
    if category in ALLOWED_CATEGORIES:
        return category
    lower = f"{title} {revelation}".lower()
    if "?" in revelation or "question" in lower:
        return "open_question"
    if "risk" in lower:
        return "risk"
    if "constraint" in lower or "must" in lower:
        return "constraint"
    if "decision" in lower or "decided" in lower:
        return "decision"
    if "should" in lower or "next step" in lower:
        return "next_step"
    if "conflict" in lower or "contradict" in lower:
        return "contradiction"
    return "finding"


def validate_synthesis_json(data: dict[str, Any], labels: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Validate an Artifact as concise synthesis rather than evidence repetition."""
    if not isinstance(data, dict):
        raise ValueError("invalid_json")
    if data.get("should_create") is False:
        return {"should_create": False, "reason": _clean(data.get("reason")) or "no_durable_insight"}

    valid = {label for label in labels if label.startswith(("C", "S"))}
    if not valid:
        return {"should_create": False, "reason": "no_grounded_material"}

    observations = []
    for raw in (data.get("conversation_observations") or data.get("observations") or [])[:3]:
        if not isinstance(raw, dict):
            continue
        citations = _citation_list(raw.get("citations"), valid)
        conversation_citations = [citation for citation in citations if citation.startswith("C")]
        if not conversation_citations:
            raise ValueError("conversation_observation_requires_conversation_citation")
        if any(labels[citation].get("is_conversation_path") is False for citation in conversation_citations):
            raise ValueError("observation_requires_chronological_conversation_citation")
        text = _clean(raw.get("text"), 800)
        if not text:
            continue
        kind = _clean(raw.get("kind") or "observation").lower()
        if kind not in {"request", "observation", "decision", "constraint", "question", "inference", "risk"}:
            kind = "observation"
        observations.append({
            "speaker": _clean(raw.get("speaker") or _speaker_for(citations, labels), 120),
            "kind": kind,
            "text": text,
            "citations": citations,
        })
    if not observations:
        return {"should_create": False, "reason": "insufficient_grounded_conversation"}

    questions = []
    for raw in (data.get("questions_raised") or [])[:6]:
        if not isinstance(raw, dict):
            continue
        citations = _citation_list(raw.get("citations"), valid)
        if not any(citation.startswith("C") for citation in citations):
            raise ValueError("question_requires_conversation_citation")
        text = _clean(raw.get("text"), 500)
        if text:
            questions.append({"text": text, "citations": citations})

    key_points = []
    seen: set[str] = set()
    for raw in (data.get("key_points") or [])[:5]:
        if not isinstance(raw, dict):
            continue
        citations = _citation_list(raw.get("citations"), valid)
        title = _clean(raw.get("title"), 120)
        revelation = _clean(raw.get("revelation") or raw.get("memory") or raw.get("content"), 360)
        if not is_semantic_title(title) or not is_revelation(revelation, citations, labels):
            continue
        signature = _normal(f"{title} {revelation}")
        if signature in seen:
            continue
        seen.add(signature)
        confidence = _clean(raw.get("confidence") or "medium").lower()
        key_points.append({
            "title": title,
            "revelation": revelation,
            # Keep this alias for pre-existing consumers, but render/extract only revelation.
            "content": revelation,
            "category": _key_point_category(title, revelation, raw.get("category")),
            "confidence": confidence if confidence in ALLOWED_CONFIDENCE else "medium",
            "citations": citations,
        })
    if len(key_points) < 3:
        return {"should_create": False, "reason": "insufficient_synthesized_key_points"}

    artifact_title = _clean(data.get("title"), 140)
    if not is_semantic_title(artifact_title):
        artifact_title = key_points[0]["title"]
    return {
        "should_create": True,
        "title": artifact_title,
        "claim_key": re.sub(r"[^a-z0-9]+", "-", _clean(data.get("claim_key") or artifact_title).lower()).strip("-")[:120],
        "conversation_observations": observations,
        "questions_raised": questions,
        "key_points": key_points,
        "key_finding": _clean(data.get("key_finding") or key_points[0]["revelation"], 1200),
        "interpretation": _clean(data.get("interpretation"), 1600),
        "recommended_next_bearing": [
            _clean(item, 240)
            for item in (data.get("recommended_next_bearing") or [])[:6]
            if _clean(item)
        ],
    }


def _cited_labels(synthesis: dict[str, Any]) -> list[str]:
    cited: list[str] = []
    for section in ("conversation_observations", "questions_raised", "key_points"):
        for item in synthesis.get(section) or []:
            for citation in item.get("citations") or []:
                if citation not in cited:
                    cited.append(citation)
    return cited


def _source_label(label: str, item: dict[str, Any]) -> str:
    if label.startswith("C"):
        return _clean(item.get("speaker") or "Quest discussion", 180)
    source = _clean(item.get("source_name") or "Quest source", 180)
    locator = _clean(item.get("locator"), 180)
    return f"{source} — {locator}" if locator else source


def render_artifact_markdown(synthesis: dict[str, Any], labels: dict[str, dict[str, Any]]) -> str:
    cited = [label for label in _cited_labels(synthesis) if label in labels]
    captured = [labels[label].get("captured_at") for label in cited if labels[label].get("captured_at")]
    window = f"{min(captured)}–{max(captured)}" if captured else "unspecified"
    lines = [
        f"# {synthesis['title']}",
        "",
        f"**Quest:** {synthesis.get('quest_title') or 'Quest'}  ",
        f"**Current Bearing:** {synthesis.get('current_bearing') or 'See Voyage Log'}  ",
        "**Artifact status:** draft  ",
        f"**Evidence window:** {window}",
        "",
        "## Conversation path",
    ]
    for number, observation in enumerate(synthesis.get("conversation_observations") or [], 1):
        cites = " ".join(f"[{citation}]" for citation in observation.get("citations") or [] if citation in labels)
        lines.extend(["", f"{number}. **{observation['speaker']} — {observation['kind'].title()}**: {observation['text']} {cites}".rstrip()])
    if synthesis.get("questions_raised"):
        lines.extend(["", "## Questions raised"])
        for question in synthesis["questions_raised"]:
            cites = " ".join(f"[{citation}]" for citation in question.get("citations") or [] if citation in labels)
            lines.append(f"- {question['text']} {cites}".rstrip())
    lines.extend(["", "## Key points"])
    for number, point in enumerate(synthesis.get("key_points") or [], 1):
        cites = " ".join(f"[{citation}]" for citation in point.get("citations") or [] if citation in labels)
        lines.extend([
            "",
            f"{number}. **{point['title']}**",
            f"**Revelation:** {point['revelation']} {cites}".rstrip(),
        ])
    if synthesis.get("interpretation"):
        lines.extend(["", "## Why this matters", synthesis["interpretation"]])
    if synthesis.get("recommended_next_bearing"):
        lines.extend(["", "## Recommended next bearing"])
        lines.extend(f"{number}. {step}" for number, step in enumerate(synthesis["recommended_next_bearing"], 1))
    lines.extend(["", "## Evidence index", "", "| ID | Source | Locator | Supports |", "|---|---|---|—|"])
    for label in cited:
        item = labels[label]
        support = []
        for point in synthesis.get("key_points") or []:
            if label in point.get("citations") or []:
                support.append(point["revelation"][:100])
        for observation in synthesis.get("conversation_observations") or []:
            if label in observation.get("citations") or []:
                support.append(observation["text"][:100])
        for question in synthesis.get("questions_raised") or []:
            if label in question.get("citations") or []:
                support.append(question["text"][:100])
        lines.append(
            f"| {label} | {_source_label(label, item).replace('|', '\\|')} | "
            f"{_clean(item.get('locator')).replace('|', '\\|')} | {'; '.join(support).replace('|', '\\|')} |"
        )
    return "\n".join(lines).rstrip() + "\n"


def _evidence_index(content: str) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    in_index = False
    for source_line in content.splitlines():
        line = source_line.strip()
        if line.lower() == "## evidence index":
            in_index = True
            continue
        if in_index and line.startswith("## "):
            break
        if not in_index or not line.startswith("|") or line.startswith("|---") or " ID " in line:
            continue
        cells = [cell.strip().replace("\\|", "|") for cell in line.strip("|").split("|")]
        if len(cells) >= 4 and re.fullmatch(r"[CSA]\d+", cells[0] or ""):
            rows[cells[0]] = {"label": cells[1], "locator": cells[2], "supports": cells[3]}
    return rows


def _category_from_revelation(title: str, revelation: str) -> str:
    return _key_point_category(title, revelation, "")


def extract_artifact_key_point_candidates(proposal) -> list[dict[str, Any]]:
    """Extract only explicit Revelation lines from a published Artifact."""
    document = getattr(proposal, "document", None)
    content = _clean(getattr(document, "current_content", None))
    if not content:
        return []
    evidence = _evidence_index(content)
    in_key_points = False
    current: dict[str, Any] | None = None
    blocks: list[dict[str, Any]] = []
    for source_line in content.splitlines():
        line = source_line.strip()
        if line.lower() == "## key points":
            in_key_points = True
            continue
        if in_key_points and line.startswith("## "):
            break
        if not in_key_points or not line:
            continue
        heading = re.match(r"^\d+\.\s+\*\*(.+?)\*\*\s*$", line)
        if heading:
            if current:
                blocks.append(current)
            current = {"title": _clean(heading.group(1), 120), "revelation": ""}
            continue
        revelation = re.match(r"^\*\*Revelation:\*\*\s*(.+)$", line, flags=re.I)
        if current and revelation:
            current["revelation"] = _clean(revelation.group(1), 420)
    if current:
        blocks.append(current)

    candidates = []
    for block in blocks[:5]:
        title = block["title"]
        raw_revelation = block["revelation"]
        citations = []
        for citation in re.findall(r"\[(C\d+|S\d+|A\d+)\]", raw_revelation):
            if citation not in citations:
                citations.append(citation)
        revelation = re.sub(r"\s*\[(?:C\d+|S\d+|A\d+)\]", "", raw_revelation).strip(" -")
        if not is_semantic_title(title) or not citations or not is_revelation(revelation):
            continue
        candidates.append({
            "title": title,
            "content": revelation,
            "category": _category_from_revelation(title, revelation),
            "confidence": "high" if any(citation.startswith("S") for citation in citations) else "medium",
            "citations": citations,
            "evidence": [
                {
                    "id": citation,
                    "label": evidence.get(citation, {}).get("label") or citation,
                    "locator": evidence.get(citation, {}).get("locator") or "",
                    "supports": evidence.get(citation, {}).get("supports") or revelation,
                }
                for citation in citations
            ],
        })
    return candidates


def _memory_fingerprint(candidate: dict[str, Any], revision: int) -> str:
    return hashlib.sha256(
        _normal(candidate["title"]).encode()
        + b"\0"
        + _normal(candidate["content"]).encode()
        + b"\0"
        + ",".join(sorted(candidate.get("citations") or [])).encode()
        + b"\0"
        + str(revision).encode()
    ).hexdigest()


def is_displayable_memory(memory) -> bool:
    """Hide old source-passage dumps while preserving manual and good memories."""
    if not getattr(memory, "artifact_id", None) or getattr(memory, "created_by", None) != "argo":
        return True
    return is_semantic_title(getattr(memory, "title", "")) and is_revelation(getattr(memory, "content", ""))


def artifact_display_title(document, proposal, loads) -> str:
    for candidate in (
        getattr(proposal, "title", None),
        getattr(document, "title", None),
    ):
        if is_semantic_title(candidate):
            return _clean(candidate, 140)
    synthesis = loads(getattr(proposal, "synthesis_json", None), {}) if proposal else {}
    for point in synthesis.get("key_points") or [] if isinstance(synthesis, dict) else []:
        title = point.get("title") if isinstance(point, dict) else None
        if is_semantic_title(title):
            return _clean(title, 140)
    return "Artifact needs a new summary"


def install_artifact_quality_contract() -> None:
    """Patch the synthesis module once, after it has completed import."""
    import src.venture_synthesis as synthesis

    if getattr(synthesis, "_artifact_quality_contract_installed", False):
        return

    synthesis.SYNTHESIS_SYSTEM_PROMPT = """\
Create a concise, Captain-reviewable Quest Artifact from supplied conversation and source evidence.
Return strict JSON only. Return {"should_create": false} if there are fewer than three distinct,
well-supported revelations. Do not invent facts, speakers, decisions, questions, or citations.

The Artifact must contain:
- title: a 3–12 word plain-language summary of the central revelation. Never use a number,
  citation, source reference, generic word (Insights, Summary, Artifact), or a copied quote.
- conversation_observations: 1–3 chronological, cited C# observations describing how the Quest
  reached this point.
- questions_raised: actionable questions from the conversation, cited with C# labels.
- key_points: 3–5 items. Each requires title, revelation, category, confidence, and C#/S# citations.

A revelation is an original, standalone statement of what the crew should retain for later work.
It must explain the implication, decision, relationship, risk, or next move derived from evidence.
Never use a source passage, verse text, transcript, numbered list item, or quotation as a revelation.
Memory will later be extracted directly from each revelation, so write concise context that is useful
without reopening the Artifact. Cite evidence separately at the end of the revelation.
"""

    def no_raw_evidence_fallback(_pack: dict[str, Any]) -> dict[str, Any]:
        # A source quote is worse than no Artifact: it creates misleading "memory".
        return {"should_create": False, "reason": "synthesis_model_unavailable"}

    original_process = synthesis._process_artifact_memory_synthesis

    def process_artifact_memory_synthesis(db, job):
        proposal = db.query(synthesis.QuestArtifactProposal).filter(
            synthesis.QuestArtifactProposal.id == job.artifact_proposal_id,
            synthesis.QuestArtifactProposal.session_id == job.quest_id,
        ).first()
        if proposal:
            existing_rows = db.query(synthesis.QuestMemoryEntry).filter(
                synthesis.QuestMemoryEntry.session_id == job.quest_id,
                synthesis.QuestMemoryEntry.artifact_id == proposal.id,
                synthesis.QuestMemoryEntry.state != "retired",
            ).all()
            for row in existing_rows:
                if not is_displayable_memory(row):
                    row.state = "retired"
                    row.updated_at = synthesis.utcnow_naive()
        return original_process(db, job)

    synthesis._fallback_synthesis = no_raw_evidence_fallback
    synthesis.validate_synthesis_json = validate_synthesis_json
    synthesis.render_artifact_markdown = render_artifact_markdown
    synthesis._extract_artifact_key_point_candidates = extract_artifact_key_point_candidates
    synthesis._process_artifact_memory_synthesis = process_artifact_memory_synthesis
    synthesis._artifact_quality_contract_installed = True
