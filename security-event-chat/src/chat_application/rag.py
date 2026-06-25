from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from chat_application.config import settings
from chat_application.cypher import extract_uuids, load_graph_schema
from chat_application.models import ChatMessage, ChatResponse, SourceHit
from chat_application.neo4j import Neo4jClient
from chat_application.ollama import OllamaClient
from chat_application.qdrant import QdrantSearchClient
from chat_application.reranker import RankedHit, graph_rows_to_hits, rrf_merge

logger = logging.getLogger(__name__)


class RagService:
    def __init__(
        self,
        *,
        ollama: OllamaClient,
        qdrant: QdrantSearchClient,
        neo4j: Neo4jClient,
    ) -> None:
        self.ollama = ollama
        self.qdrant = qdrant
        self.neo4j = neo4j
        self._schema = load_graph_schema()

    async def ask(self, message: str, history: list[ChatMessage]) -> ChatResponse:
        started = time.perf_counter()
        limit = settings.retrieval_limit

        event_ids = extract_uuids(message)

        vector_task = asyncio.create_task(self._search_vector(message, limit))
        bm25_task = asyncio.create_task(asyncio.to_thread(self._search_bm25, message, limit))
        cypher_task = asyncio.create_task(self._search_graph(message))
        exact_task = (
            asyncio.create_task(self._lookup_exact_event_ids(event_ids))
            if event_ids
            else None
        )

        vector_hits, bm25_hits, graph_result = await asyncio.gather(
            vector_task, bm25_task, cypher_task
        )

        exact_hits: list[dict[str, Any]] = []
        exact_graph_rows: list[dict[str, Any]] = []
        if exact_task is not None:
            exact_hits, exact_graph_rows = await exact_task

        graph_rows = list(exact_graph_rows)
        seen_graph = {json.dumps(row, sort_keys=True, default=str) for row in graph_rows}
        for row in graph_result["rows"]:
            key = json.dumps(row, sort_keys=True, default=str)
            if key not in seen_graph:
                graph_rows.append(row)
                seen_graph.add(key)
        graph_result = {**graph_result, "rows": graph_rows}

        graph_hits = graph_rows_to_hits(graph_result["rows"])
        merged = self._merge_with_exact(exact_hits, vector_hits, bm25_hits, graph_hits)
        retrieval_ms = (time.perf_counter() - started) * 1000

        context = self._build_context(
            merged,
            graph_result["rows"],
            graph_result.get("cypher"),
            graph_unavailable=graph_result.get("graph_unavailable", False),
        )
        chat_history = [{"role": m.role, "content": m.content} for m in history[-8:]]

        gen_started = time.perf_counter()
        answer = await self.ollama.synthesize_answer(message, context, chat_history)
        generation_ms = (time.perf_counter() - gen_started) * 1000

        return ChatResponse(
            answer=answer,
            sources=[self._to_source(hit) for hit in merged],
            cypher=graph_result.get("cypher"),
            graph_rows=graph_result["rows"][:20],
            retrieval_ms=round(retrieval_ms, 1),
            generation_ms=round(generation_ms, 1),
        )

    async def _search_vector(self, query: str, limit: int) -> list[dict[str, Any]]:
        try:
            vector = await self.ollama.embed(query)
            return await asyncio.to_thread(self.qdrant.search_vector, vector, limit=limit)
        except Exception as exc:
            logger.warning("vector search failed: %s", exc)
            return []

    def _search_bm25(self, query: str, limit: int) -> list[dict[str, Any]]:
        try:
            return self.qdrant.search_bm25(query, limit=limit)
        except Exception as exc:
            logger.warning("BM25 search failed: %s", exc)
            return []

    async def _lookup_exact_event_ids(
        self, event_ids: list[str]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        hits: list[dict[str, Any]] = []
        graph_rows: list[dict[str, Any]] = []

        for event_id in event_ids:
            qdrant_hit = await asyncio.to_thread(self.qdrant.fetch_by_event_id, event_id)
            if qdrant_hit is not None:
                hits.append(qdrant_hit)

            try:
                rows = await self.neo4j.lookup_instruction_for_event(event_id)
            except Exception as exc:
                logger.warning("exact graph lookup failed for %s: %s", event_id, exc)
                rows = []

            for row in rows:
                if row.get("instruction_id"):
                    graph_rows.append(row)

        return hits, graph_rows

    @staticmethod
    def _merge_with_exact(
        exact_hits: list[dict[str, Any]],
        vector_hits: list[dict[str, Any]],
        bm25_hits: list[dict[str, Any]],
        graph_hits: list[dict[str, Any]],
    ) -> list[RankedHit]:
        merged = rrf_merge([vector_hits, bm25_hits, graph_hits])
        if not exact_hits:
            return merged[: settings.max_context_hits]

        exact_ranked = rrf_merge([exact_hits])
        pinned_keys = {hit.key for hit in exact_ranked}
        remainder = [hit for hit in merged if hit.key not in pinned_keys]
        return (exact_ranked + remainder)[: settings.max_context_hits]

    async def _search_graph(self, question: str) -> dict[str, Any]:
        cypher: str | None = None
        try:
            cypher = await self.ollama.generate_cypher(question, self._schema)
            rows = await self.neo4j.run_cypher(cypher)
            return {"cypher": cypher, "rows": rows}
        except ValueError as exc:
            # Log the rejected query for auditability but do NOT surface it
            # upward — the caller will still use vector and BM25 results.
            logger.warning(
                "Cypher validation rejected LLM query — %s | query=%r", exc, cypher
            )
            return {"cypher": None, "rows": [], "graph_unavailable": True}
        except Exception as exc:
            logger.warning("graph search failed: %s", exc)
            return {"cypher": None, "rows": [], "graph_unavailable": True}

    @staticmethod
    def _build_context(
        hits: list[RankedHit],
        graph_rows: list[dict[str, Any]],
        cypher: str | None,
        *,
        graph_unavailable: bool = False,
    ) -> str:
        sections: list[str] = []

        if graph_unavailable:
            sections.append(
                "Note: graph search was unavailable for this question. "
                "Answer using the retrieved vector and BM25 results below only."
            )

        if cypher:
            sections.append(f"Neo4j Cypher executed:\n{cypher}")

        if graph_rows:
            sections.append(
                "Neo4j graph results:\n"
                + json.dumps(graph_rows[:20], indent=2, default=str)
            )

        if hits:
            event_lines: list[str] = []
            for index, hit in enumerate(hits, start=1):
                merged = hit.merged or {}
                instruction_block = ""
                if hit.instruction:
                    instr = hit.instruction
                    instruction_block = (
                        f"\n  instruction: id={instr.get('instruction_id')} "
                        f"type={instr.get('instruction_type')} "
                        f"currency={instr.get('currency')} "
                        f"status={instr.get('status')} "
                        f"wire_scope={instr.get('wire_scope')} "
                        f"owning_lob={instr.get('owning_lob')}"
                    )
                event_lines.append(
                    f"[{index}] event_id={hit.event_id} instruction_id={hit.instruction_id} "
                    f"sources={sorted(hit.sources)} score={hit.score:.4f}\n"
                    f"  action={merged.get('action')} severity={merged.get('severity')} "
                    f"actor={merged.get('actor_user_id')} creator={merged.get('creator_user_id')} "
                    f"approver={merged.get('approver_user_id')} rejector={merged.get('rejector_user_id')}\n"
                    f"  wire_scope={merged.get('wire_scope')} instruction_type={merged.get('instruction_type')} "
                    f"owning_lob={merged.get('owning_lob')} status={merged.get('status')}\n"
                    f"  summary: {hit.summary}{instruction_block}"
                )
            sections.append("Retrieved security events (merged vector + BM25 + graph):\n" + "\n".join(event_lines))

        if not sections:
            return "No indexed security events or graph results were found."
        return "\n\n".join(sections)

    @staticmethod
    def _to_source(hit: RankedHit) -> SourceHit:
        return SourceHit(
            event_id=hit.event_id,
            instruction_id=hit.instruction_id,
            score=round(hit.score, 4),
            sources=sorted(hit.sources),
            summary=hit.summary,
            merged=hit.merged,
            security_event=hit.security_event,
        )
