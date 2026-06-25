from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from security_event_chat.config import settings
from security_event_chat.cypher import extract_event_id, row_summary


@dataclass
class RankedHit:
    key: str
    event_id: str | None
    instruction_id: str | None
    score: float
    sources: set[str] = field(default_factory=set)
    summary: str = ""
    merged: dict[str, Any] | None = None
    security_event: dict[str, Any] | None = None
    instruction: dict[str, Any] | None = None
    graph_row: dict[str, Any] | None = None


def _hit_key(event_id: str | None, instruction_id: str | None, summary: str) -> str:
    if event_id:
        return f"event:{event_id}"
    if instruction_id:
        return f"instruction:{instruction_id}"
    return f"row:{hash(summary)}"


def _summary_from_qdrant(hit: dict[str, Any]) -> str:
    merged = hit.get("merged") or {}
    parts = [
        merged.get("action"),
        merged.get("severity"),
        merged.get("actor_user_id"),
        merged.get("creator_user_id"),
        merged.get("message") or hit.get("search_text", "")[:200],
    ]
    return " · ".join(str(p) for p in parts if p)


def rrf_merge(
    ranked_lists: list[list[dict[str, Any]]],
    *,
    k: int | None = None,
) -> list[RankedHit]:
    """Reciprocal rank fusion across vector, BM25, and Neo4j hit lists."""
    k = k or settings.rrf_k
    combined: dict[str, RankedHit] = {}

    for hits in ranked_lists:
        for rank, hit in enumerate(hits, start=1):
            source = hit.get("source", "unknown")
            event_id = hit.get("event_id")
            instruction_id = hit.get("instruction_id")
            summary = hit.get("summary") or _summary_from_qdrant(hit)
            key = _hit_key(event_id, instruction_id, summary)

            if key not in combined:
                combined[key] = RankedHit(
                    key=key,
                    event_id=event_id,
                    instruction_id=instruction_id,
                    score=0.0,
                    summary=summary,
                    merged=hit.get("merged"),
                    security_event=hit.get("security_event"),
                    instruction=hit.get("instruction"),
                    graph_row=hit.get("graph_row"),
                )
            entry = combined[key]
            entry.score += 1.0 / (k + rank)
            entry.sources.add(source)
            if not entry.merged and hit.get("merged"):
                entry.merged = hit["merged"]
            if not entry.security_event and hit.get("security_event"):
                entry.security_event = hit["security_event"]
            if not entry.instruction and hit.get("instruction"):
                entry.instruction = hit["instruction"]
            if not entry.graph_row and hit.get("graph_row"):
                entry.graph_row = hit["graph_row"]
            if len(summary) > len(entry.summary):
                entry.summary = summary

    return sorted(combined.values(), key=lambda item: item.score, reverse=True)


def graph_rows_to_hits(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for row in rows:
        event_id = extract_event_id(row)
        instruction_id = row.get("instruction_id")
        if instruction_id is not None:
            instruction_id = str(instruction_id)
        else:
            for value in row.values():
                if isinstance(value, dict) and value.get("instruction_id"):
                    instruction_id = str(value["instruction_id"])
                    break
        hits.append(
            {
                "source": "neo4j",
                "score": 1.0,
                "event_id": event_id,
                "instruction_id": instruction_id,
                "summary": row_summary(row),
                "graph_row": row,
            }
        )
    return hits
