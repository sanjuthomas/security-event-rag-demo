from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from chat_application.config import settings
from chat_application.cypher import (
    extract_uuids,
    load_graph_schema,
    normalize_read_only_cypher,
    plan_graph_queries,
    validate_read_only_cypher,
)
from chat_application.models import ChatMessage, ChatResponse, SearchMode, SourceHit
from chat_application.neo4j import Neo4jClient
from chat_application.ollama import OllamaClient
from chat_application.qdrant import QdrantSearchClient
from chat_application.reranker import RankedHit, graph_rows_to_hits, rrf_merge

logger = logging.getLogger(__name__)


def _parse_authorization_basis(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(item) for item in parsed if item]
        except json.JSONDecodeError:
            pass
    return []


def _display_from_snap_user(snap: dict[str, Any], field: str) -> str:
    user = snap.get(field) or {}
    family_name = user.get("family_name")
    given_name = user.get("given_name")
    user_id = user.get("user_id") or ""
    if family_name and given_name:
        return f"{family_name}, {given_name} ({user_id})"
    return user_id


def _instruction_lifecycle_party_lines(payload: dict[str, Any], snap: dict[str, Any]) -> str:
    status = (snap.get("status") or payload.get("status") or "").upper()
    lines: list[str] = []

    if status == "REJECTED":
        rejected_by = payload.get("rejector_display") or _display_from_snap_user(snap, "rejected_by")
        if rejected_by:
            lines.append(f"rejected_by={rejected_by}")
        rejected_at = payload.get("rejected_at") or snap.get("rejected_at")
        if rejected_at:
            lines.append(f"rejected_at={rejected_at}")
        reason = payload.get("rejection_reason") or snap.get("rejection_reason")
        if reason:
            lines.append(f"rejection_reason={reason}")
    elif status in ("STANDING", "SINGLE_USE", "USED", "SUSPENDED"):
        approver = payload.get("approver_display") or _display_from_snap_user(snap, "approved_by")
        if approver:
            lines.append(f"approver={approver}")
        approved_at = payload.get("approved_at") or snap.get("approved_at")
        if approved_at:
            lines.append(f"approved_at={approved_at}")

    return "\n  ".join(lines)


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

    async def ask(
        self,
        message: str,
        history: list[ChatMessage],
        *,
        mode: SearchMode = "events",
    ) -> ChatResponse:
        """Run the full RAG pipeline.

        mode="events"       — instruction + payment security events in Qdrant
        mode="instructions" — instruction_state points only
        mode="payments"     — payment_fact points only
        mode="all"          — all points (no source filter)
        """
        started = time.perf_counter()
        limit = settings.retrieval_limit

        # Map mode to Qdrant source filter tag
        qdrant_source: str | None
        if mode == "events":
            qdrant_source = "security_events"
        elif mode == "instructions":
            qdrant_source = "instruction_state"
        elif mode == "payments":
            qdrant_source = "payment"
        else:
            qdrant_source = None  # no filter — search everything

        event_ids = extract_uuids(message)

        vector_task = asyncio.create_task(
            self._search_vector(message, limit, source=qdrant_source)
        )
        bm25_task = asyncio.create_task(
            asyncio.to_thread(self._search_bm25, message, limit, qdrant_source)
        )
        cypher_task = asyncio.create_task(self._search_graph(message, mode=mode))
        exact_task = (
            asyncio.create_task(self._lookup_exact_event_ids(event_ids))
            if event_ids and mode != "instructions"
            else None
        )
        instruction_exact_task = (
            asyncio.create_task(self._lookup_exact_instruction_ids(event_ids, message))
            if event_ids and mode == "instructions"
            else None
        )

        vector_hits, bm25_hits, graph_result = await asyncio.gather(
            vector_task, bm25_task, cypher_task
        )

        exact_hits: list[dict[str, Any]] = []
        exact_graph_rows: list[dict[str, Any]] = []
        if exact_task is not None:
            exact_hits, exact_graph_rows = await exact_task
        if instruction_exact_task is not None:
            exact_hits.extend(await instruction_exact_task)

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
            mode=mode,
        )
        chat_history = [{"role": m.role, "content": m.content} for m in history[-8:]]

        gen_started = time.perf_counter()
        answer = await self._synthesize_instruction_approval_answer(
            message, event_ids, merged, graph_result["rows"]
        )
        if answer is None:
            answer = await self.ollama.synthesize_answer(
                message, context, chat_history, mode=mode
            )
        generation_ms = (time.perf_counter() - gen_started) * 1000

        return ChatResponse(
            answer=answer,
            sources=[self._to_source(hit) for hit in merged],
            cypher=graph_result.get("cypher"),
            graph_rows=graph_result["rows"][:20],
            retrieval_ms=round(retrieval_ms, 1),
            generation_ms=round(generation_ms, 1),
        )

    async def _search_vector(
        self, query: str, limit: int, source: str | None = None
    ) -> list[dict[str, Any]]:
        try:
            vector = await self.ollama.embed(query)
            return await asyncio.to_thread(
                self.qdrant.search_vector, vector, limit=limit, source=source
            )
        except Exception as exc:
            logger.warning("vector search failed: %s", exc)
            return []

    def _search_bm25(
        self, query: str, limit: int, source: str | None = None
    ) -> list[dict[str, Any]]:
        try:
            return self.qdrant.search_bm25(query, limit=limit, source=source)
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

    async def _lookup_exact_instruction_ids(
        self, instruction_ids: list[str], message: str
    ) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        approval_question = "approv" in message.lower()

        for instruction_id in instruction_ids:
            state_hit = await asyncio.to_thread(
                self.qdrant.fetch_by_instruction_id, instruction_id
            )
            if state_hit is not None:
                hits.append(state_hit)

            if approval_question:
                approve_hits = await asyncio.to_thread(
                    self.qdrant.fetch_instruction_approve_events, instruction_id
                )
                hits.extend(approve_hits)

        return hits

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

    async def _search_graph(
        self, question: str, *, mode: SearchMode = "events"
    ) -> dict[str, Any]:
        planned = plan_graph_queries(question, mode=mode)
        if planned is not None:
            try:
                return await self._run_planned_graph_queries(planned)
            except Exception as exc:
                logger.warning("planned graph query failed: %s", exc)

        cypher: str | None = None
        try:
            cypher = await self.ollama.generate_cypher(question, self._schema, mode=mode)
            cypher = normalize_read_only_cypher(cypher)
            rows = await self.neo4j.run_cypher(cypher)
            return {"cypher": cypher, "rows": rows}
        except ValueError as exc:
            logger.warning(
                "Cypher validation rejected LLM query — %s | query=%r", exc, cypher
            )
            fallback = (
                plan_graph_queries(question, mode=mode) if planned is None else None
            )
            if fallback is not None:
                try:
                    return await self._run_planned_graph_queries(fallback)
                except Exception as fallback_exc:
                    logger.warning("planned graph fallback failed: %s", fallback_exc)
            return {"cypher": cypher, "rows": [], "graph_unavailable": True}
        except Exception as exc:
            logger.warning("graph search failed: %s", exc)
            return {"cypher": None, "rows": [], "graph_unavailable": True}

    async def _run_planned_graph_queries(
        self, planned: list[tuple[str, str]]
    ) -> dict[str, Any]:
        cyphers: list[str] = []
        rows: list[dict[str, Any]] = []
        for _label, query in planned:
            normalized = normalize_read_only_cypher(query)
            validate_read_only_cypher(normalized)
            cyphers.append(normalized)
            rows.extend(await self.neo4j.run_cypher(normalized))
        return {"cypher": "\n\n".join(cyphers), "rows": rows}

    async def _synthesize_instruction_approval_answer(
        self,
        message: str,
        instruction_ids: list[str],
        hits: list[RankedHit],
        graph_rows: list[dict[str, Any]],
    ) -> str | None:
        """Return Who/When/Why when OPA authorization is in context; LLM rewrites WHY for readability."""
        if "approv" not in message.lower() or not instruction_ids:
            return None

        target_id = instruction_ids[0]
        approver: str | None = None
        when: str | None = None
        summary: str | None = None
        basis: list[str] = []

        for row in graph_rows:
            row_id = row.get("v.instruction_id") or row.get("instruction_id")
            if str(row_id) != target_id:
                continue
            summary = row.get("v.authorization_summary") or row.get("authorization_summary")
            approver = row.get("approver_display")
            when = row.get("v.approved_at") or row.get("approved_at")
            basis = _parse_authorization_basis(
                row.get("v.authorization_basis") or row.get("authorization_basis")
            )
            break

        for hit in hits:
            payload = hit.merged or {}
            payload_id = hit.instruction_id or payload.get("instruction_id")
            if str(payload_id) != target_id:
                continue
            summary = summary or payload.get("authorization_summary")
            approver = approver or payload.get("approver_display") or payload.get("actor_display")
            when = when or payload.get("approved_at") or payload.get("timestamp")
            if not basis:
                basis = _parse_authorization_basis(payload.get("authorization_basis"))
            if summary:
                break

        if not approver or not summary:
            return None

        why = await self.ollama.summarize_authorization_why(
            approver=approver,
            authorization_summary=summary,
            authorization_basis=basis or None,
        )

        when_line = f"WHEN: {when}" if when else None
        return "\n".join(
            line
            for line in (
                f"WHO: {approver}",
                when_line,
                f"WHY: {why}",
            )
            if line
        )

    @staticmethod
    def _build_context(
        hits: list[RankedHit],
        graph_rows: list[dict[str, Any]],
        cypher: str | None,
        *,
        graph_unavailable: bool = False,
        mode: SearchMode = "events",
    ) -> str:
        sections: list[str] = []

        if mode == "instructions":
            sections.append("Search mode: INSTRUCTIONS (instruction master graph — independent of security events)")
        elif mode == "all":
            sections.append(
                "Search mode: ALL ENTITIES (instructions, payments, and all security events)"
            )
        elif mode == "payments":
            sections.append("Search mode: PAYMENTS (payment records only)")
        elif mode == "events":
            sections.append(
                "Search mode: SECURITY EVENTS (instruction + payment security event log)"
            )

        if graph_unavailable:
            if mode == "instructions":
                sections.append(
                    "Note: instruction graph search was unavailable. "
                    "Do not infer hierarchy or structural relationships from vector/BM25 hits."
                )
            else:
                sections.append(
                    "Note: graph search was unavailable for this question. "
                    "Answer using the retrieved vector and BM25 results below only."
                )

        if cypher:
            sections.append(f"Neo4j Cypher executed:\n{cypher}")

        if graph_rows:
            ranking_rows = [
                row
                for row in graph_rows
                if "alert_count" in row and "actor_display" in row
            ]
            if ranking_rows:
                sections.append(
                    "Neo4j user ranking by policy alerts (instruction + payment combined):\n"
                    + json.dumps(ranking_rows[:20], indent=2, default=str)
                )
            aggregate = next(
                (
                    row
                    for row in graph_rows
                    if any(key in row for key in ("total", "count"))
                    and "alert_count" not in row
                    and len(row) <= 3
                ),
                None,
            )
            if aggregate is not None:
                total = aggregate.get("total", aggregate.get("count"))
                sections.append(f"Neo4j aggregate count: {total}")
            detail_rows = [
                row for row in graph_rows if row not in ranking_rows and row is not aggregate
            ] if ranking_rows or aggregate else graph_rows
            if detail_rows:
                sections.append(
                    "Neo4j graph results:\n"
                    + json.dumps(detail_rows[:20], indent=2, default=str)
                )
        elif cypher and not graph_unavailable:
            sections.append(
                "Neo4j graph results: 0 rows — the graph query found no matching records. "
                "For structural questions (supervisor relationships, hierarchy violations, "
                "cross-approvals) this means no such case exists in the data. "
                "Do NOT use vector/BM25 hits to contradict this finding."
            )

        if hits:
            lines: list[str] = []
            for index, hit in enumerate(hits, start=1):
                payload = hit.merged or {}
                snap = payload.get("instruction_snapshot") or {}
                src = payload.get("source") or (sorted(hit.sources)[0] if hit.sources else "?")
                if src in {"vector", "bm25", "exact"} and payload.get("source"):
                    src = payload.get("source")

                if src == "instruction_state" or src == "exact_instruction":
                    party_lines = _instruction_lifecycle_party_lines(payload, snap)
                    lines.append(
                        f"[{index}] INSTRUCTION instruction_id={hit.instruction_id} "
                        f"score={hit.score:.4f}\n"
                        f"  status={snap.get('status')} type={snap.get('instruction_type')} "
                        f"lob={snap.get('owning_lob')} currency={snap.get('currency')} "
                        f"scope={snap.get('wire_scope')}\n"
                        f"  creditor={snap.get('creditor_name')} "
                        f"creditor_acct={snap.get('creditor_account_id')}\n"
                        f"  creator={payload.get('creator_display')}\n"
                        f"  {party_lines}\n"
                        f"  why={payload.get('authorization_summary') or ''}\n"
                        f"  basis={' | '.join(payload.get('authorization_basis') or [])}\n"
                        f"  effective={snap.get('effective_date')} end={snap.get('end_date')} "
                        f"expired={snap.get('is_expired', False)}"
                    )
                elif src == "payment_fact":
                    psnap = payload.get("payment_snapshot") or {}
                    lines.append(
                        f"[{index}] PAYMENT payment_id={payload.get('payment_id')} "
                        f"instruction_id={payload.get('instruction_id')} score={hit.score:.4f}\n"
                        f"  status={payload.get('status', psnap.get('status'))} "
                        f"amount={payload.get('amount', psnap.get('amount'))} "
                        f"currency={payload.get('currency', psnap.get('currency'))} "
                        f"lob={payload.get('owning_lob', psnap.get('owning_lob'))}\n"
                        f"  value_date={payload.get('value_date', psnap.get('value_date'))}\n"
                        f"  creator={payload.get('creator_display')} "
                        f"approver={payload.get('approver_display')}"
                    )
                elif src == "payment_security_event":
                    psnap = payload.get("payment_snapshot") or {}
                    lines.append(
                        f"[{index}] PAYMENT SECURITY EVENT event_id={hit.event_id} "
                        f"payment_id={payload.get('payment_id')} "
                        f"instruction_id={payload.get('instruction_id')} score={hit.score:.4f}\n"
                        f"  time={payload.get('timestamp')} action={payload.get('action')} "
                        f"severity={payload.get('severity')} outcome={payload.get('outcome')} "
                        f"actor={payload.get('actor_display')}\n"
                        f"  amount={payload.get('amount', psnap.get('amount'))} "
                        f"currency={payload.get('currency', psnap.get('currency'))} "
                        f"lob={payload.get('owning_lob', psnap.get('owning_lob'))}\n"
                        f"  why={payload.get('authorization_summary') or payload.get('reason') or payload.get('message', hit.summary)}\n"
                        f"  basis={' | '.join(payload.get('authorization_basis') or [])}"
                    )
                elif src in ("instruction_security_event", "security_event", "exact_approve_event"):
                    merged = hit.merged or {}
                    event_snap = merged.get("instruction_snapshot") or {}
                    status = (merged.get("status") or event_snap.get("status") or "").upper()
                    action = (merged.get("action") or "").upper()
                    if status == "REJECTED" or action == "REJECT":
                        party_lines = (
                            f"rejected_by={merged.get('rejector_display') or merged.get('actor_display', merged.get('actor_user_id'))}\n"
                            f"  rejected_at={merged.get('rejected_at') or event_snap.get('rejected_at') or merged.get('timestamp')}\n"
                            f"  rejection_reason={merged.get('rejection_reason') or event_snap.get('rejection_reason') or ''}"
                        )
                    else:
                        party_lines = (
                            f"approver={merged.get('approver_display', merged.get('approver_user_id'))}\n"
                            f"  approved_at={merged.get('approved_at') or event_snap.get('approved_at') or ''}"
                        )
                    lines.append(
                        f"[{index}] INSTRUCTION SECURITY EVENT event_id={hit.event_id} "
                        f"instruction_id={hit.instruction_id} "
                        f"sources={sorted(hit.sources)} score={hit.score:.4f}\n"
                        f"  time={merged.get('timestamp')} action={merged.get('action')} "
                        f"severity={merged.get('severity')} outcome={merged.get('outcome')} "
                        f"actor={merged.get('actor_display', merged.get('actor_user_id'))} "
                        f"lob={merged.get('owning_lob')}\n"
                        f"  creator={merged.get('creator_display', merged.get('creator_user_id'))}\n"
                        f"  {party_lines}\n"
                        f"  why={merged.get('authorization_summary') or merged.get('event_reason') or merged.get('reason') or hit.summary}\n"
                        f"  basis={' | '.join(merged.get('authorization_basis') or [])}"
                    )
                else:
                    merged = hit.merged or {}
                    lines.append(
                        f"[{index}] UNKNOWN source={src} event_id={hit.event_id} "
                        f"instruction_id={hit.instruction_id} score={hit.score:.4f}\n"
                        f"  summary: {hit.summary or merged.get('message', '')}"
                    )
            label = {
                "events": "Retrieved security events (instruction + payment)",
                "instructions": "Retrieved instruction states",
                "payments": "Retrieved payment records",
                "all": "Retrieved results across all entity types",
            }.get(mode, "Retrieved results")
            sections.append(f"{label} (vector + BM25 + graph):\n" + "\n".join(lines))

        if not sections:
            return "No indexed data was found."
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
