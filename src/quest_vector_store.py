"""Quest-isolated Chroma vector storage for Argos Venture evidence."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Iterable

from src.embedding_lanes import build_embedding_lanes, dedupe_results, query_lanes

logger = logging.getLogger(__name__)

LANE_CAPTAIN = "captain"
LANE_SHARED = "shared"
LANE_SUMMARIES = "summaries"
VALID_VISIBILITY_LANES = {LANE_CAPTAIN, LANE_SHARED, LANE_SUMMARIES}


def _quest_hash(quest_id: str) -> str:
    return hashlib.sha256(str(quest_id).encode("utf-8")).hexdigest()[:24]


def collection_name_for_quest(quest_id: str, visibility_lane: str = LANE_SHARED) -> str:
    lane = visibility_lane if visibility_lane in VALID_VISIBILITY_LANES else LANE_SHARED
    return f"venture_q_{_quest_hash(quest_id)}_{lane}"


def deterministic_chunk_doc_id(
    quest_id: str,
    source_id: str,
    source_version_id: str,
    artifact_id: str,
    chunk_index: int,
) -> str:
    opaque_quest = _quest_hash(quest_id)
    return (
        f"quest:{opaque_quest}:source:{source_id}:version:{source_version_id}:"
        f"artifact:{artifact_id}:chunk:{int(chunk_index)}"
    )


@dataclass
class QuestVectorWriteResult:
    indexed: int = 0
    deduplicated: int = 0
    failed: int = 0
    unavailable: bool = False
    error: str | None = None


def _lanes_for(quest_id: str, visibility_lane: str):
    return build_embedding_lanes(collection_name_for_quest(quest_id, visibility_lane))


def ensure_quest_vector_store_available(quest_id: str, visibility_lane: str = LANE_SHARED) -> None:
    lanes = _lanes_for(quest_id, visibility_lane)
    if not lanes:
        raise RuntimeError("vector_store_unavailable")


def index_evidence_chunks(chunks: Iterable[dict[str, Any]]) -> QuestVectorWriteResult:
    """Write Quest evidence chunks to physically isolated Chroma collections.

    Each chunk dict must contain ``quest_id``, ``visibility_lane``,
    ``chroma_document_id``, ``content`` and ``metadata``. Existing ids are
    treated as retry deduplications.
    """
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for chunk in chunks:
        text = chunk.get("content")
        if not text or not isinstance(text, str):
            continue
        quest_id = str(chunk.get("quest_id") or "")
        lane = str(chunk.get("visibility_lane") or LANE_SHARED)
        if not quest_id:
            continue
        grouped.setdefault((quest_id, lane), []).append(chunk)

    result = QuestVectorWriteResult()
    for (quest_id, lane), rows in grouped.items():
        try:
            lanes = _lanes_for(quest_id, lane)
        except Exception as exc:
            logger.warning("Quest vector store unavailable for %s/%s: %s", quest_id, lane, exc)
            result.unavailable = True
            result.error = "vector_store_unavailable"
            continue
        if not lanes:
            result.unavailable = True
            result.error = "embedding_unavailable"
            continue
        ids = [str(r["chroma_document_id"]) for r in rows]
        texts = [str(r["content"]) for r in rows]
        metas = [dict(r.get("metadata") or {}) for r in rows]
        for embed_lane in lanes:
            try:
                existing = embed_lane.collection.get(ids=ids)
                existing_ids = set(existing.get("ids") or [])
            except Exception:
                existing_ids = set()
            new = [(i, t, m) for i, t, m in zip(ids, texts, metas) if i not in existing_ids]
            if existing_ids:
                result.deduplicated += len(existing_ids)
            if not new:
                continue
            for start in range(0, len(new), 100):
                batch = new[start:start + 100]
                batch_ids = [r[0] for r in batch]
                batch_texts = [r[1] for r in batch]
                batch_metas = [r[2] for r in batch]
                try:
                    embed_lane.collection.add(
                        ids=batch_ids,
                        embeddings=embed_lane.encode(batch_texts),
                        documents=batch_texts,
                        metadatas=batch_metas,
                    )
                    result.indexed += len(batch_ids)
                except Exception as exc:
                    logger.warning("Quest vector write failed in %s: %s", embed_lane.collection_name, exc)
                    result.failed += len(batch_ids)
    return result


def search_quest_evidence(
    quest_id: str,
    query: str,
    visibility_lanes: list[str],
    *,
    limit: int = 8,
    source_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Search only the active Quest's permitted evidence collections."""
    out: list[dict[str, Any]] = []
    where: dict[str, Any] = {"is_current": True}
    if source_ids:
        where = {"$and": [where, {"source_id": {"$in": source_ids}}]}
    for lane in visibility_lanes:
        if lane not in VALID_VISIBILITY_LANES:
            continue
        try:
            lanes = _lanes_for(quest_id, lane)
            results = query_lanes(
                lanes,
                query,
                n_results=lambda _lane: max(limit * 2, limit),
                include=["documents", "metadatas", "distances"],
                where=where,
            )
        except Exception as exc:
            logger.debug("Quest vector search skipped for %s/%s: %s", quest_id, lane, exc)
            continue
        for _embed_lane, payload in results:
            ids = payload.get("ids") or [[]]
            docs = payload.get("documents") or [[]]
            metas = payload.get("metadatas") or [[]]
            distances = payload.get("distances") or [[]]
            for doc_id, doc, meta, dist in zip(ids[0] or [], docs[0] or [], metas[0] or [], distances[0] or []):
                out.append({
                    "id": doc_id,
                    "document": doc,
                    "metadata": meta or {},
                    "distance": float(dist) if dist is not None else None,
                })
    out.sort(key=lambda r: (r.get("distance") is None, r.get("distance") or 0.0))
    return dedupe_results(out, id_key="id", limit=limit)


def delete_source_version(quest_id: str, source_version_id: str, visibility_lanes: list[str] | None = None) -> None:
    lanes_to_scan = visibility_lanes or sorted(VALID_VISIBILITY_LANES)
    for lane in lanes_to_scan:
        try:
            for embed_lane in _lanes_for(quest_id, lane):
                embed_lane.collection.delete(where={"source_version_id": source_version_id})
        except Exception:
            logger.debug("Quest vector delete_source_version skipped", exc_info=True)


def delete_source(quest_id: str, source_id: str, visibility_lanes: list[str] | None = None) -> None:
    lanes_to_scan = visibility_lanes or sorted(VALID_VISIBILITY_LANES)
    for lane in lanes_to_scan:
        try:
            for embed_lane in _lanes_for(quest_id, lane):
                embed_lane.collection.delete(where={"source_id": source_id})
        except Exception:
            logger.debug("Quest vector delete_source skipped", exc_info=True)


def delete_where(quest_id: str, where: dict[str, Any], visibility_lanes: list[str] | None = None) -> None:
    lanes_to_scan = visibility_lanes or sorted(VALID_VISIBILITY_LANES)
    for lane in lanes_to_scan:
        try:
            for embed_lane in _lanes_for(quest_id, lane):
                embed_lane.collection.delete(where=where)
        except Exception:
            logger.debug("Quest vector delete_where skipped", exc_info=True)
